"""INFRA-046：scripts/db_reset_local.py（just db-reset）重建本機 DB。

unit 案例以 monkeypatch 取代連線、子行程與 port 探測；integration 案例只操作 `reset_probe_<uuid8>`
暫存 database，不碰共用的 postgres database。
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import psycopg
import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from psycopg import sql

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "db_reset_local.py"
ALEMBIC_INI = REPO_ROOT / "apps" / "api" / "alembic.ini"
OWNER_URL = "postgresql://postgres:postgres@127.0.0.1:54342/postgres"
BACKEND_URL = "postgresql://app_backend:app_backend_local@127.0.0.1:54342/postgres"
_LIBPQ_ENV = ("PGHOST", "PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE")


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("db_reset_local_under_test", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def reset(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    for name in _LIBPQ_ENV:
        monkeypatch.delenv(name, raising=False)
    return _load_script()


@pytest.fixture
def connect_calls(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    calls: list[object] = []

    def _fake_connect(*args: object, **kwargs: object) -> None:
        calls.append(args)
        raise AssertionError("不應連線")

    monkeypatch.setattr(psycopg, "connect", _fake_connect)
    return calls


# ---------------------------------------------------------------------------
# unit
# ---------------------------------------------------------------------------


def test_db_reset_rejects_remote_owner_url(
    reset: ModuleType, connect_calls: list[object], monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(ValueError, match="只允許本機 loopback DB"):
        reset.reset_database("x", owner_url="postgresql://postgres:p@db.example.com:5432/postgres")
    assert connect_calls == []

    monkeypatch.setenv("PGHOSTADDR", "10.0.0.8")
    with pytest.raises(ValueError, match="只允許本機 loopback DB"):
        reset.reset_database("x")
    assert connect_calls == []


def test_db_reset_main_rejects_args(
    reset: ModuleType, connect_calls: list[object], monkeypatch: pytest.MonkeyPatch
) -> None:
    runs: list[object] = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: runs.append(a))

    assert reset.main(["--db-url", "x"]) == 2
    assert connect_calls == []
    assert runs == []


def test_db_reset_db_down_message(
    reset: ModuleType,
    connect_calls: list[object],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(reset, "local_db_reachable", lambda: False)

    assert reset.main([]) == 1
    assert "just db-start" in capsys.readouterr().err
    assert connect_calls == []


def test_db_reset_upgrade_failure_surfaces_stderr(
    reset: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}

    def _fake_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen["args"] = args
        seen["env"] = kwargs["env"]
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="boom-marker")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    monkeypatch.setenv("PGHOSTADDR", "127.0.0.1")
    monkeypatch.setenv("PGSERVICE", "local")

    with pytest.raises(RuntimeError, match="boom-marker"):
        reset.upgrade_head("reset_probe_x")

    assert "PGHOSTADDR" not in seen["env"]
    assert "PGSERVICE" not in seen["env"]
    assert seen["env"]["MIGRATION_DATABASE_URL"].endswith("@127.0.0.1:54342/reset_probe_x")
    assert seen["args"][-2:] == ["upgrade", "head"]


# ---------------------------------------------------------------------------
# integration（只操作 reset_probe_ 暫存 database）
# ---------------------------------------------------------------------------


def _owner(dbname: str = "postgres") -> psycopg.Connection[Any]:
    conn = psycopg.connect(OWNER_URL, dbname=dbname, autocommit=True, connect_timeout=3)
    assert conn.info.hostaddr == "127.0.0.1"
    return conn


@pytest.fixture
def probe_db() -> Iterator[str]:
    name = f"reset_probe_{uuid.uuid4().hex[:8]}"
    with _owner() as conn:
        conn.execute(sql.SQL("create database {}").format(sql.Identifier(name)))
    try:
        yield name
    finally:
        with _owner() as conn:
            conn.execute(
                sql.SQL("drop database if exists {} with (force)").format(sql.Identifier(name))
            )


@pytest.mark.integration
def test_db_reset_recreates_probe_database(reset: ModuleType, probe_db: str) -> None:
    with _owner(probe_db) as conn:
        conn.execute("create table junk (id int)")

    reset.reset_database(probe_db)

    with _owner(probe_db) as conn:
        row = conn.execute("select to_regclass('public.junk')").fetchone()
    assert row == (None,)

    head = reset.upgrade_head(probe_db)

    heads = ScriptDirectory.from_config(Config(str(ALEMBIC_INI))).get_heads()
    assert [head] == heads
    with _owner(probe_db) as conn:
        proc = conn.execute(
            "select to_regprocedure('app_private.grant_backend(regclass,text)') is not null"
        ).fetchone()
    assert proc == (True,)


@pytest.mark.integration
def test_db_reset_sets_local_backend_password(reset: ModuleType, probe_db: str) -> None:
    # 角色是 cluster 層級：先在暫存 database 套用 baseline，確保 app_backend 存在
    reset.upgrade_head(probe_db)

    reset.set_local_backend_password()

    with psycopg.connect(BACKEND_URL, connect_timeout=3) as conn:
        row = conn.execute(
            "select current_user, r.rolsuper, r.rolbypassrls from pg_roles r"
            " where r.rolname = current_user"
        ).fetchone()
    assert row == ("app_backend", False, False)
