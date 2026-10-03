#!/usr/bin/env python3
"""INFRA-020：比對 SQLAlchemy metadata（app.models.Base）與套用全部 revision 後的本機 DB。

以 Alembic autogenerate 的 `compare_metadata` 取得差異，轉成 Drift 清單
（docs/architecture_decisions.md §5「schema drift」）：比對表、欄位（型別、nullable）、FK、unique
constraint；不比對 index、check constraint、server default、trigger 與 function（由各表的 migration
測試負責）。無法對應的差異一律以 `unsupported` 回報，不靜默丟棄。

用 apps/api/.venv 的 python 執行（just schema-drift），只讀不寫。
exit code：0 沒有 drift；1 有 drift（逐行印 `kind  target  detail`）；
2 參數 / 連線 / 匯入失敗等無法給出結論。
"""

from __future__ import annotations

import argparse
import importlib
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

import psycopg
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Connection, MetaData, create_engine
from sqlalchemy.schema import ForeignKeyConstraint, SchemaItem, Table, UniqueConstraint

API_DIR = Path(__file__).resolve().parents[1] / "apps" / "api"
DEFAULT_DB_URL = "postgresql+psycopg://postgres:postgres@127.0.0.1:54342/postgres"
_EXCLUDED_TABLES = frozenset({"alembic_version"})


@dataclass(frozen=True)
class Drift:
    kind: str
    target: str
    detail: str


def _make_include_object(ignore_tables: frozenset[str]) -> Any:
    def include_object(
        obj: SchemaItem, name: str | None, type_: str, reflected: bool, compare_to: Any
    ) -> bool:
        if type_ == "index":
            return False
        return not (type_ == "table" and name in (_EXCLUDED_TABLES | ignore_tables))

    return include_object


def include_object(
    obj: SchemaItem, name: str | None, type_: str, reflected: bool, compare_to: Any
) -> bool:
    """預設的排除規則（不含呼叫端的 ignore_tables）：index 與 alembic_version 表不比對。"""
    return bool(_make_include_object(frozenset())(obj, name, type_, reflected, compare_to))


def _columns_target(table_name: str, columns: list[str]) -> str:
    return f"{table_name}.{','.join(columns)}"


def _convert_single(diff: tuple[Any, ...]) -> Drift:
    op = diff[0]
    if op in ("add_table", "remove_table"):
        table: Table = diff[1]
        kind = "table_missing_in_db" if op == "add_table" else "table_missing_in_model"
        return Drift(kind, table.name, op)
    if op in ("add_column", "remove_column"):
        _, _, table_name, column = diff
        kind = "column_missing_in_db" if op == "add_column" else "column_missing_in_model"
        return Drift(kind, f"{table_name}.{column.name}", f"{op} {column.type}")
    if op in ("add_fk", "remove_fk"):
        fk: ForeignKeyConstraint = diff[1]
        side = "model" if op == "add_fk" else "db"
        target = _columns_target(fk.table.name, [str(c) for c in fk.column_keys])
        refs = ",".join(e.target_fullname for e in fk.elements)
        return Drift("fk_mismatch", target, f"只有 {side} 有 FK → {refs}")
    if op in ("add_constraint", "remove_constraint") and isinstance(diff[1], UniqueConstraint):
        uc: UniqueConstraint = diff[1]
        side = "model" if op == "add_constraint" else "db"
        target = _columns_target(uc.table.name, [c.name for c in uc.columns])
        return Drift("unique_mismatch", target, f"只有 {side} 有 unique constraint")
    return Drift("unsupported", str(op), repr(diff))


def _convert_modify(diff: tuple[Any, ...]) -> Drift:
    op, _, table_name, column_name, _existing_kw, existing, model_value = diff
    target = f"{table_name}.{column_name}"
    if op == "modify_type":
        return Drift("type_mismatch", target, f"model={model_value} db={existing}")
    if op == "modify_nullable":
        detail = f"model nullable={model_value} db nullable={existing}"
        return Drift("nullable_mismatch", target, detail)
    return Drift("unsupported", target, repr(diff))


def convert(diffs: list[Any]) -> list[Drift]:
    drifts: list[Drift] = []
    for diff in diffs:
        if isinstance(diff, list):
            # modify_* 以清單成組回傳（同一欄位的多項變更）
            drifts.extend(_convert_modify(item) for item in diff)
        else:
            drifts.append(_convert_single(diff))
    return sorted(drifts, key=lambda d: (d.kind, d.target))


def compare(
    metadata: MetaData,
    conn: Connection,
    schema: str = "public",
    ignore_tables: frozenset[str] = frozenset(),
) -> list[Drift]:
    opts: dict[str, Any] = {
        "compare_type": True,
        "compare_server_default": False,
        "include_object": _make_include_object(ignore_tables),
    }
    if schema != "public":
        opts["include_schemas"] = True
        opts["include_name"] = lambda name, type_, parent_names: (
            name == schema if type_ == "schema" else True
        )
    context = MigrationContext.configure(conn, opts=opts)
    return convert(compare_metadata(context, metadata))


def _import(name: str) -> ModuleType:
    """在 sys.path 加入 apps/api 後才匯入（app.models、tests.support.db_urls）。"""
    return importlib.import_module(name)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="比對 app.models.Base 與本機 DB 的 schema drift")
    parser.add_argument("--db-url", default=DEFAULT_DB_URL)
    parser.add_argument("--schema", default="public")
    parser.add_argument("--ignore-table", action="append", default=[])
    args = parser.parse_args(argv)

    if str(API_DIR) not in sys.path:
        sys.path.insert(0, str(API_DIR))

    db_urls = _import("tests.support.db_urls")
    url = db_urls.to_sqlalchemy_url(args.db_url)
    try:
        db_urls.assert_loopback(url)
    except ValueError as exc:
        print(f"錯誤：{exc}", file=sys.stderr)
        return 2

    try:
        base = _import("app.models").Base
    except (ImportError, AttributeError) as exc:
        print(f"錯誤：找不到 app.models.Base（{exc}）", file=sys.stderr)
        return 2

    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            driver_conn = conn.connection.driver_connection
            if not isinstance(driver_conn, psycopg.Connection):
                raise TypeError("預期 psycopg 連線")
            db_urls.assert_connected_loopback(driver_conn.info.hostaddr)
            drifts = compare(
                base.metadata, conn, schema=args.schema, ignore_tables=frozenset(args.ignore_table)
            )
    except Exception as exc:  # 連線 / 反射失敗一律無法給出結論
        print(f"錯誤：{exc.__class__.__name__}: {exc}", file=sys.stderr)
        return 2
    finally:
        engine.dispose()

    if drifts:
        for d in drifts:
            print(f"{d.kind}  {d.target}  {d.detail}")
        return 1
    print(f"沒有 schema drift（比對 {len(base.metadata.tables)} 張表，schema={args.schema}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
