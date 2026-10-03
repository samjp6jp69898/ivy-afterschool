"""INFRA-020：scripts/check_schema_drift.py 以 Alembic compare_metadata 比對 metadata 與 DB。

compare 的案例在 local_db_conn 的 transaction 內建立唯一名稱的暫存 schema（drift_probe_<uuid8>）
與表，以測試內定義的 MetaData(schema=...) 比對，結束時 rollback；SQLAlchemy 連線以 creator 包住
同一條 local_db_conn，沿用其 loopback 守衛。
"""

from __future__ import annotations

import importlib.util
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import psycopg
import pytest
from sqlalchemy import (
    Column,
    Connection,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    Table,
    Text,
    Uuid,
    create_engine,
    text,
)
from sqlalchemy.pool import StaticPool

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_schema_drift.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_schema_drift_under_test", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def drift() -> ModuleType:
    return _load_script()


@pytest.fixture
def sa_conn(local_db_conn: psycopg.Connection[Any]) -> Iterator[Connection]:
    engine = create_engine(
        "postgresql+psycopg://", creator=lambda: local_db_conn, poolclass=StaticPool
    )
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            yield conn
        finally:
            trans.rollback()


@pytest.fixture
def schema(sa_conn: Connection) -> str:
    name = f"drift_probe_{uuid.uuid4().hex[:8]}"
    sa_conn.execute(text(f"create schema {name}"))
    return name


def _ddl(conn: Connection, schema: str, *statements: str) -> None:
    conn.execute(text(f"set local search_path to {schema}"))
    for statement in statements:
        conn.execute(text(statement))


_T_DDL = "create table t (id uuid primary key, name text not null, created_at timestamptz not null)"


def _t_table(metadata: MetaData, **overrides: Column[Any]) -> Table:
    columns = {
        "id": Column("id", Uuid, primary_key=True),
        "name": Column("name", Text, nullable=False),
        "created_at": Column("created_at", DateTime(timezone=True), nullable=False),
    }
    columns.update(overrides)
    return Table("t", metadata, *columns.values())


def _kinds(drifts: list[Any]) -> list[tuple[str, str]]:
    return [(d.kind, d.target) for d in drifts]


def test_schema_drift_identical_returns_empty(
    drift: ModuleType, sa_conn: Connection, schema: str
) -> None:
    _ddl(sa_conn, schema, _T_DDL)
    metadata = MetaData(schema=schema)
    _t_table(metadata)

    assert drift.compare(metadata, sa_conn, schema=schema) == []


def test_schema_drift_column_missing_in_db(
    drift: ModuleType, sa_conn: Connection, schema: str
) -> None:
    _ddl(sa_conn, schema, _T_DDL)
    metadata = MetaData(schema=schema)
    _t_table(metadata, note=Column("note", Text))

    assert _kinds(drift.compare(metadata, sa_conn, schema=schema)) == [
        ("column_missing_in_db", "t.note")
    ]


def test_schema_drift_column_missing_in_model(
    drift: ModuleType, sa_conn: Connection, schema: str
) -> None:
    _ddl(sa_conn, schema, _T_DDL, "alter table t add column extra int")
    metadata = MetaData(schema=schema)
    _t_table(metadata)

    assert _kinds(drift.compare(metadata, sa_conn, schema=schema)) == [
        ("column_missing_in_model", "t.extra")
    ]


def test_schema_drift_nullable_mismatch(
    drift: ModuleType, sa_conn: Connection, schema: str
) -> None:
    _ddl(sa_conn, schema, _T_DDL)
    metadata = MetaData(schema=schema)
    _t_table(metadata, name=Column("name", Text, nullable=True))

    assert _kinds(drift.compare(metadata, sa_conn, schema=schema)) == [
        ("nullable_mismatch", "t.name")
    ]


def test_schema_drift_timestamptz_vs_naive(
    drift: ModuleType, sa_conn: Connection, schema: str
) -> None:
    _ddl(sa_conn, schema, _T_DDL)
    metadata = MetaData(schema=schema)
    _t_table(metadata, created_at=Column("created_at", DateTime(), nullable=False))

    result = drift.compare(metadata, sa_conn, schema=schema)

    assert _kinds(result) == [("type_mismatch", "t.created_at")]
    assert "model=" in result[0].detail
    assert "db=" in result[0].detail


def test_schema_drift_fk_mismatch(drift: ModuleType, sa_conn: Connection, schema: str) -> None:
    _ddl(
        sa_conn,
        schema,
        "create table parent (id int primary key)",
        "create table child (id int primary key, parent_id int references parent(id))",
    )
    metadata = MetaData(schema=schema)
    Table("parent", metadata, Column("id", Integer, primary_key=True))
    Table("child", metadata, Column("id", Integer, primary_key=True), Column("parent_id", Integer))

    result = drift.compare(metadata, sa_conn, schema=schema)

    assert len(result) == 1
    assert result[0].kind == "fk_mismatch"
    assert result[0].target.startswith("child.")
    assert "parent_id" in result[0].target


def test_schema_drift_fk_matches_when_declared(
    drift: ModuleType, sa_conn: Connection, schema: str
) -> None:
    _ddl(
        sa_conn,
        schema,
        "create table parent (id int primary key)",
        "create table child (id int primary key, parent_id int references parent(id))",
    )
    metadata = MetaData(schema=schema)
    Table("parent", metadata, Column("id", Integer, primary_key=True))
    Table(
        "child",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("parent_id", Integer, ForeignKey(f"{schema}.parent.id")),
    )

    assert drift.compare(metadata, sa_conn, schema=schema) == []


def test_schema_drift_unique_mismatch(drift: ModuleType, sa_conn: Connection, schema: str) -> None:
    _ddl(sa_conn, schema, "create table u (id int primary key, a int, b int, unique (a, b))")
    metadata = MetaData(schema=schema)
    Table(
        "u",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("a", Integer),
        Column("b", Integer),
    )

    result = drift.compare(metadata, sa_conn, schema=schema)

    assert [d.kind for d in result] == ["unique_mismatch"]
    assert result[0].target == "u.a,b"


def test_schema_drift_ignores_indexes_and_alembic_version(
    drift: ModuleType, sa_conn: Connection, schema: str
) -> None:
    _ddl(
        sa_conn,
        schema,
        _T_DDL,
        "create index t_name_partial on t (name) where name is not null",
        "create table alembic_version (version_num varchar(32) primary key)",
    )
    metadata = MetaData(schema=schema)
    _t_table(metadata)

    assert drift.compare(metadata, sa_conn, schema=schema) == []


def test_schema_drift_table_ignore_list(
    drift: ModuleType, sa_conn: Connection, schema: str
) -> None:
    _ddl(sa_conn, schema, _T_DDL, "create table orphan (id int primary key)")
    metadata = MetaData(schema=schema)
    _t_table(metadata)

    assert (
        drift.compare(metadata, sa_conn, schema=schema, ignore_tables=frozenset({"orphan"})) == []
    )
    assert _kinds(drift.compare(metadata, sa_conn, schema=schema)) == [
        ("table_missing_in_model", "orphan")
    ]


def test_schema_drift_results_sorted(drift: ModuleType, sa_conn: Connection, schema: str) -> None:
    _ddl(
        sa_conn,
        schema,
        _T_DDL,
        "alter table t add column zz int",
        "alter table t add column aa int",
    )
    metadata = MetaData(schema=schema)
    _t_table(metadata, note=Column("note", Text))

    assert _kinds(drift.compare(metadata, sa_conn, schema=schema)) == [
        ("column_missing_in_db", "t.note"),
        ("column_missing_in_model", "t.aa"),
        ("column_missing_in_model", "t.zz"),
    ]


def test_schema_drift_main_missing_models(
    drift: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    connects: list[object] = []
    monkeypatch.setattr(psycopg, "connect", lambda *a, **k: connects.append(a))
    # sys.modules 中的 None 讓 import 拋 ModuleNotFoundError
    monkeypatch.setitem(sys.modules, "app.models", None)

    assert drift.main([]) == 2
    assert "找不到 app.models.Base" in capsys.readouterr().err
    assert connects == []


def test_schema_drift_main_rejects_remote(
    drift: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    connects: list[object] = []
    monkeypatch.setattr(psycopg, "connect", lambda *a, **k: connects.append(a))

    assert drift.main(["--db-url", "postgresql+psycopg://u:p@db.example.com/postgres"]) == 2
    assert connects == []
    assert "只允許本機 loopback DB" in capsys.readouterr().err


def test_schema_drift_unknown_diff_reported_as_unsupported(drift: ModuleType) -> None:
    unknown = ("add_table_comment", "t", "說明")

    result = drift.convert([unknown])

    assert [(d.kind, d.target) for d in result] == [("unsupported", "add_table_comment")]
    assert "說明" in result[0].detail
