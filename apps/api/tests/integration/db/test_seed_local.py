"""DB-039：本機專用 supabase/seed.sql（app_backend 本機密碼，不含任何帳號）。

以同目錄 conftest 的 `backend_conn`（app_backend 實際登入 `TEST_DB_BACKEND_URL`）驗證；只連本機
loopback，登入失敗（seed 沒生效）時整個測試中止並提示 just db-reset，不會改用 owner。
"""

from pathlib import Path

import psycopg
import pytest

from tests.integration.db.conftest import Conn

_REPO_ROOT = Path(__file__).resolve().parents[5]
_SEED_PATH = _REPO_ROOT / "supabase" / "seed.sql"


def test_seed_local_app_backend_can_login(backend_conn: Conn) -> None:
    row = backend_conn.execute("select current_user, session_user").fetchone()
    assert row == ("app_backend", "app_backend")


def test_seed_local_app_backend_not_privileged(backend_conn: Conn) -> None:
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
