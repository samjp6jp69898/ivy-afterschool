"""DB-039：本機專用 supabase/seed.sql（app_backend 本機密碼，不含任何帳號）。

依 tdd.notes，本檔自行以 psycopg 連 `TEST_DB_BACKEND_URL`（app_backend 實際登入），不依賴 DB-002 的
fixture（DB-002 依賴本 task）。只連本機 loopback；登入失敗視為測試失敗（seed 沒生效），
不會改用 owner。
"""

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict

_DEFAULT_BACKEND_URL = "postgresql://app_backend:app_backend_local@127.0.0.1:54342/postgres"
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_LOOPBACK_ADDRS = frozenset({"127.0.0.1", "::1"})
_REPO_ROOT = Path(__file__).resolve().parents[5]
_SEED_PATH = _REPO_ROOT / "supabase" / "seed.sql"

BackendConn = psycopg.Connection[tuple[Any, ...]]


def _backend_dsn() -> str:
    """讀 TEST_DB_BACKEND_URL（預設本機 app_backend），依 libpq 規則拒絕任何非 loopback 目標。"""
    url = os.environ.get("TEST_DB_BACKEND_URL", _DEFAULT_BACKEND_URL)
    dsn = url.replace("postgresql+psycopg://", "postgresql://", 1)
    try:
        params = conninfo_to_dict(dsn)
    except psycopg.ProgrammingError as exc:
        pytest.exit(f"TEST_DB_BACKEND_URL 不是合法的連線字串：{exc}", returncode=2)
    if params.get("service") or os.environ.get("PGSERVICE"):
        pytest.exit("只允許本機 loopback DB：拒絕 service 連線設定", returncode=2)
    host = params.get("host") or os.environ.get("PGHOST")
    if not host:
        pytest.exit("只允許本機 loopback DB：未指定 host", returncode=2)
    for item in str(host).split(","):
        if item not in _LOOPBACK_HOSTS:
            pytest.exit(f"只允許本機 loopback DB：host={item}", returncode=2)
    hostaddr = params.get("hostaddr") or os.environ.get("PGHOSTADDR")
    for item in str(hostaddr or "").split(","):
        if item and item not in _LOOPBACK_ADDRS:
            pytest.exit(f"只允許本機 loopback DB：hostaddr={item}", returncode=2)
    return dsn


@pytest.fixture
def backend_conn() -> Iterator[BackendConn]:
    try:
        conn = psycopg.connect(_backend_dsn())
    except psycopg.OperationalError as exc:
        pytest.fail(
            f"app_backend 無法登入本機 DB（{exc}）：seed.sql 未生效，先跑 just db-reset --yes",
            pytrace=False,
        )
    try:
        if conn.info.hostaddr not in _LOOPBACK_ADDRS:
            hostaddr = conn.info.hostaddr
            conn.close()
            pytest.exit(f"只允許本機 loopback DB：實際連線位址 {hostaddr}", returncode=2)
        yield conn
    finally:
        conn.rollback()
        conn.close()


def test_seed_local_app_backend_can_login(backend_conn: BackendConn) -> None:
    row = backend_conn.execute("select current_user, session_user").fetchone()
    assert row == ("app_backend", "app_backend")


def test_seed_local_app_backend_not_privileged(backend_conn: BackendConn) -> None:
    attrs = backend_conn.execute(
        "select rolsuper, rolbypassrls from pg_roles where rolname = current_user"
    ).fetchall()
    assert attrs == [(False, False)]

    (public_usage,) = backend_conn.execute(
        "select has_schema_privilege('public', 'usage')"
    ).fetchone() or (None,)
    assert public_usage is True

    with pytest.raises(psycopg.errors.InsufficientPrivilege) as exc_info:
        backend_conn.execute("set role postgres")
    assert exc_info.value.sqlstate == "42501"


def test_seed_local_file_has_no_accounts() -> None:
    assert _SEED_PATH.is_file(), f"找不到 {_SEED_PATH}"
    text = _SEED_PATH.read_text(encoding="utf-8")
    assert "$argon2" not in text

    lowered = text.lower()
    for forbidden in (
        "insert into public.staff_users",
        "insert into staff_users",
        "insert into public.parent_accounts",
        "insert into parent_accounts",
    ):
        assert forbidden not in lowered, forbidden

    # 本機密碼確實由 seed 設定
    assert "alter role app_backend login password 'app_backend_local'" in lowered
