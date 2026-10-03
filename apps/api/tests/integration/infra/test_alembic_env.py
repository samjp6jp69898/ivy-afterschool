"""INFRA-045：Alembic 骨架（alembic.ini、env.py、forward-only revision 範本）。

每個需要 DB 的測試以 owner 連線建立暫存 database `alembic_probe_<uuid8>`（只用於建立 / 刪除探針
database 與查驗結果）；revision 放在 tmp_path，不依賴 alembic/versions 的正式 revision。
"""

import configparser
import importlib.util
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from psycopg import sql

from tests.support import db_urls

API_DIR = Path(__file__).resolve().parents[3]
ALEMBIC_INI = API_DIR / "alembic.ini"
PROBE_REV = "probe001"
BACKEND_URL = "postgresql+psycopg://app_backend:app_backend_local@127.0.0.1:54342/postgres"

_PROBE_REVISION = f'''"""probe"""

from alembic import op

revision = "{PROBE_REV}"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("create table probe (v text)")
    op.execute("insert into probe select current_setting('lock_timeout')")


def downgrade() -> None:
    raise NotImplementedError
'''

Conn = psycopg.Connection[tuple[Any, ...]]


def _owner_connect(dbname: str = "postgres") -> Conn:
    dsn = db_urls.to_psycopg_dsn(db_urls.owner_url())
    conn = psycopg.connect(dsn, dbname=dbname, autocommit=True)
    db_urls.assert_connected_loopback(conn.info.hostaddr)
    return conn


@pytest.fixture
def probe_db() -> Iterator[str]:
    name = f"alembic_probe_{uuid4().hex[:8]}"
    with _owner_connect() as conn:
        conn.execute(sql.SQL("create database {}").format(sql.Identifier(name)))
    try:
        yield name
    finally:
        with _owner_connect() as conn:
            conn.execute(
                sql.SQL("drop database if exists {} with (force)").format(sql.Identifier(name))
            )


def _probe_url(name: str, scheme: str = "postgresql+psycopg://") -> str:
    return f"{scheme}postgres:postgres@127.0.0.1:54342/{name}"


@pytest.fixture
def alembic_cfg(tmp_path: Path) -> Config:
    (tmp_path / f"{PROBE_REV}_probe.py").write_text(_PROBE_REVISION, encoding="utf-8")
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("version_locations", str(tmp_path))
    return cfg


def _fetch_one(dbname: str, query: str) -> Any:
    with _owner_connect(dbname) as conn:
        row = conn.execute(query).fetchone()
    assert row is not None
    return row[0]


def test_alembic_env_applies_and_commits(
    probe_db: str, alembic_cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MIGRATION_DATABASE_URL", _probe_url(probe_db))

    command.upgrade(alembic_cfg, "head")

    # 另開連線看得到資料：migration 確實 commit，且執行時 lock_timeout 已設為 10s
    assert _fetch_one(probe_db, "select v from probe") == "10s"
    assert _fetch_one(probe_db, "select version_num from alembic_version") == PROBE_REV


def test_alembic_env_requires_migration_url(
    probe_db: str, alembic_cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MIGRATION_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", BACKEND_URL)

    with pytest.raises(RuntimeError, match="MIGRATION_DATABASE_URL"):
        command.upgrade(alembic_cfg, "head")

    assert _fetch_one(probe_db, "select to_regclass('public.alembic_version') is null") is True


def test_alembic_env_normalizes_plain_scheme(
    probe_db: str, alembic_cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MIGRATION_DATABASE_URL", _probe_url(probe_db, scheme="postgresql://"))

    command.upgrade(alembic_cfg, "head")

    assert _fetch_one(probe_db, "select version_num from alembic_version") == PROBE_REV


def test_alembic_revision_template_is_forward_only(tmp_path: Path) -> None:
    versions = tmp_path / "versions"
    versions.mkdir()
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("version_locations", str(versions))

    command.revision(cfg, message="probe", rev_id="zz999")

    generated = versions / "zz999_probe.py"
    assert [p.name for p in versions.glob("*.py")] == ["zz999_probe.py"]
    spec = importlib.util.spec_from_file_location("zz999_probe", generated)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "zz999"
    assert module.down_revision is None
    with pytest.raises(NotImplementedError, match="forward-only"):
        module.downgrade()


def test_alembic_ini_has_no_url() -> None:
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(ALEMBIC_INI, encoding="utf-8")

    assert parser.has_section("alembic")
    assert not parser.has_option("alembic", "sqlalchemy.url")
    assert parser.get("alembic", "file_template") == "%%(rev)s_%%(slug)s"
    assert parser.get("alembic", "script_location") == "%(here)s/alembic"
