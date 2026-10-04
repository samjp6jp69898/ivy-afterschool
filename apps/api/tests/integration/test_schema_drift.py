"""BACKEND-024：SQLAlchemy metadata 對本機 DB 的 schema drift 整合測試（§5）。

以 INFRA-020 ``scripts/check_schema_drift.py::compare`` 比對 ``app.models.Base.metadata`` 與套用全部
Alembic revision 後的 public schema；index、check constraint、server default 不在比對範圍。
連線用 owner（讀 information_schema 需要），先 assert_loopback。
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import Connection, MetaData, create_engine

from app.models import Base
from tests.support import db_urls

# 營運模組尚未建 model 的表；每完成一個 models task 就從這裡移除對應表
PENDING_MODEL_TABLES = frozenset(
    {
        "devices",
        "nfc_cards",
    }
)


def _load_checker() -> ModuleType:
    path = Path(__file__).resolve().parents[4] / "scripts" / "check_schema_drift.py"
    spec = importlib.util.spec_from_file_location("check_schema_drift", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclass 解析型別註記需要
    spec.loader.exec_module(module)
    return module


checker = _load_checker()


@pytest.fixture(scope="module")
def conn() -> Iterator[Connection]:
    url = db_urls.owner_url()
    db_urls.assert_loopback(url)
    engine = create_engine(db_urls.to_sqlalchemy_url(url))
    try:
        with engine.connect() as connection:
            yield connection
    finally:
        engine.dispose()


def test_schema_drift_modeled_tables(conn: Connection) -> None:
    drifts = checker.compare(Base.metadata, conn)

    others = [d for d in drifts if d.kind != "table_missing_in_model"]
    assert others == [], "\n".join(f"{d.kind}  {d.target}  {d.detail}" for d in others)


def test_schema_drift_every_table_modeled(conn: Connection) -> None:
    drifts = checker.compare(Base.metadata, conn)

    missing = {d.target for d in drifts if d.kind == "table_missing_in_model"}
    assert missing - PENDING_MODEL_TABLES == set(), "DB 有表但沒有 model，請補 model"


def test_schema_drift_pending_tables_not_modeled() -> None:
    stale = PENDING_MODEL_TABLES & set(Base.metadata.tables)

    assert stale == set(), "請從 PENDING_MODEL_TABLES 移除 " + ", ".join(sorted(stale))


def test_schema_drift_detects_nullable_mutation(conn: Connection) -> None:
    mutated = MetaData()
    for table in Base.metadata.tables.values():
        table.to_metadata(mutated)
    mutated.tables["students"].c.name.nullable = True

    drifts = checker.compare(mutated, conn)

    mismatches = [d for d in drifts if d.kind == "nullable_mismatch"]
    assert [d.target for d in mismatches] == ["students.name"]
