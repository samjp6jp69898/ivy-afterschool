"""DB-002：DB 整合測試共用 fixture（owner / backend 連線、角色切換、SQLSTATE 斷言、factories）。"""

from types import SimpleNamespace

import psycopg
import pytest

from tests.integration.db.conftest import (
    CHECK_VIOLATION,
    EXCLUSION_VIOLATION,
    FK_VIOLATION,
    INSUFFICIENT_PRIVILEGE,
    NOT_NULL_VIOLATION,
    RAISE_EXCEPTION,
    SEEDED_UPDATED_AT,
    UNIQUE_VIOLATION,
    Conn,
    as_role,
    assert_backend_read_write,
    assert_table_secured,
    assert_updated_at_trigger,
    connect_backend,
    connect_owner,
    pg_error,
)
from tests.integration.db.factories import insert_row
from tests.support import db_urls


def _scalar(conn: Conn, sql: str) -> object:
    row = conn.execute(sql).fetchone()
    assert row is not None, f"查詢沒有回傳任何列：{sql}"
    return row[0]


# 以下兩個測試依定義順序執行：第一個建表後 rollback，第二個必須看不到
def test_fixture_rollback_isolation_step1_creates_table(owner_conn: Conn) -> None:
    owner_conn.execute("create table public._iso(id int)")
    owner_conn.execute("insert into public._iso values (1)")
    assert _scalar(owner_conn, "select count(*) from public._iso") == 1


def test_fixture_rollback_isolation_step2_table_not_visible(
    owner_conn: Conn, backend_conn: Conn
) -> None:
    assert _scalar(owner_conn, "select to_regclass('public._iso')") is None
    assert _scalar(backend_conn, "select to_regclass('public._iso')") is None


def test_fixture_backend_conn_is_app_backend(backend_conn: Conn) -> None:
    assert _scalar(backend_conn, "select current_user") == "app_backend"
    assert _scalar(backend_conn, "select rolsuper from pg_roles where rolname = current_user") is (
        False
    )


def test_fixture_backend_conn_no_owner_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "TEST_DB_BACKEND_URL", "postgresql://app_backend:wrong@127.0.0.1:54342/postgres"
    )

    def _owner_url_forbidden() -> str:
        raise AssertionError("backend 連線失敗時不得改用 owner_url()")

    monkeypatch.setattr(db_urls, "owner_url", _owner_url_forbidden)

    with pytest.raises(pytest.exit.Exception) as exc_info:
        connect_backend()
    assert "just db-reset" in exc_info.value.msg
    assert exc_info.value.returncode == 2


def test_fixture_pg_error_constants() -> None:
    assert {
        "UNIQUE_VIOLATION": UNIQUE_VIOLATION,
        "CHECK_VIOLATION": CHECK_VIOLATION,
        "FK_VIOLATION": FK_VIOLATION,
        "NOT_NULL_VIOLATION": NOT_NULL_VIOLATION,
        "INSUFFICIENT_PRIVILEGE": INSUFFICIENT_PRIVILEGE,
        "EXCLUSION_VIOLATION": EXCLUSION_VIOLATION,
        "RAISE_EXCEPTION": RAISE_EXCEPTION,
    } == {
        "UNIQUE_VIOLATION": "23505",
        "CHECK_VIOLATION": "23514",
        "FK_VIOLATION": "23503",
        "NOT_NULL_VIOLATION": "23502",
        "INSUFFICIENT_PRIVILEGE": "42501",
        "EXCLUSION_VIOLATION": "23P01",
        "RAISE_EXCEPTION": "P0001",
    }


def test_fixture_pg_error_matches_sqlstate(owner_conn: Conn) -> None:
    owner_conn.execute("create table public._pg_err(code text unique)")
    owner_conn.execute("insert into public._pg_err values ('A')")

    with pg_error(owner_conn, UNIQUE_VIOLATION):
        owner_conn.execute("insert into public._pg_err values ('A')")
    # 已 rollback 到 savepoint，同一個 transaction 可以繼續
    owner_conn.execute("insert into public._pg_err values ('B')")
    assert _scalar(owner_conn, "select count(*) from public._pg_err") == 2

    with (
        pytest.raises(AssertionError, match=r"23514.*23505"),
        pg_error(owner_conn, CHECK_VIOLATION),
    ):
        owner_conn.execute("insert into public._pg_err values ('A')")
    assert _scalar(owner_conn, "select count(*) from public._pg_err") == 2


def test_fixture_pg_error_fails_when_no_error_raised(owner_conn: Conn) -> None:
    with pytest.raises(AssertionError, match="23505"), pg_error(owner_conn, UNIQUE_VIOLATION):
        owner_conn.execute("select 1")
    assert _scalar(owner_conn, "select 2") == 2


def test_fixture_as_role_switches_and_resets(owner_conn: Conn) -> None:
    with as_role(owner_conn, "anon"):
        assert _scalar(owner_conn, "select current_user") == "anon"
    assert _scalar(owner_conn, "select current_user") == "postgres"


@pytest.mark.parametrize("role", ["postgres; drop", "postgres", "ANON", ""])
def test_fixture_as_role_rejects_unknown_role(owner_conn: Conn, role: str) -> None:
    with pytest.raises(ValueError, match="as_role"), as_role(owner_conn, role):
        pass
    assert _scalar(owner_conn, "select current_user") == "postgres"


def test_fixture_refuses_remote_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_DB_OWNER_URL", "postgresql://x@db.example.com/postgres")

    def _connect_forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("非本機 host 不得嘗試連線")

    monkeypatch.setattr(psycopg, "connect", _connect_forbidden)

    with pytest.raises(pytest.exit.Exception) as exc_info:
        connect_owner()
    assert "只允許本機 loopback DB" in exc_info.value.msg
    assert "db.example.com" in exc_info.value.msg
    assert exc_info.value.returncode == 2


class _FakeConn:
    def __init__(self, hostaddr: str) -> None:
        self.info = SimpleNamespace(hostaddr=hostaddr)
        self.closed = False

    def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize("connect", [connect_backend, connect_owner], ids=["backend", "owner"])
def test_fixture_rejects_connected_non_loopback(
    monkeypatch: pytest.MonkeyPatch, connect: object
) -> None:
    fake = _FakeConn("10.0.0.11")
    monkeypatch.setattr(psycopg, "connect", lambda *args, **kwargs: fake)

    with pytest.raises(pytest.exit.Exception) as exc_info:
        connect()  # type: ignore[operator]
    assert "10.0.0.11" in exc_info.value.msg
    assert exc_info.value.returncode == 2
    assert fake.closed is True


def test_fixture_assert_table_secured_passes_for_secured_table(owner_conn: Conn) -> None:
    owner_conn.execute("create table public._sec(id int)")
    owner_conn.execute("call app_private.secure_table('public._sec')")

    assert_table_secured(owner_conn, "public._sec")
    # 檢查結束後角色已還原、transaction 仍可用
    assert _scalar(owner_conn, "select current_user") == "postgres"


def test_fixture_assert_table_secured_fails_without_rls(owner_conn: Conn) -> None:
    owner_conn.execute("create table public._unsec(id int)")

    with pytest.raises(AssertionError, match="_unsec"):
        assert_table_secured(owner_conn, "public._unsec")
    assert _scalar(owner_conn, "select current_user") == "postgres"


def test_fixture_insert_row_returns_inserted_row(owner_conn: Conn) -> None:
    owner_conn.execute(
        "create table public._ins(id int generated always as identity, name text, "
        "phone text default '0912-000-001')"
    )

    row = insert_row(owner_conn, "public._ins", name="王小明")

    assert row == {"id": 1, "name": "王小明", "phone": "0912-000-001"}
    assert _scalar(owner_conn, "select name from public._ins where id = 1") == "王小明"


def test_fixture_pg_error_exposes_constraint_name(owner_conn: Conn) -> None:
    owner_conn.execute("create table public._pg_named(code text constraint uq_pg_named unique)")
    owner_conn.execute("insert into public._pg_named values ('A')")

    with pg_error(owner_conn, UNIQUE_VIOLATION) as err:
        owner_conn.execute("insert into public._pg_named values ('A')")

    assert err.constraint_name == "uq_pg_named"
    assert err.error is not None
    assert err.error.sqlstate == "23505"


_UPD_TABLE = (
    "create table public._upd(id uuid primary key default gen_random_uuid(), name text, "
    "updated_at timestamptz not null default now())"
)


def test_fixture_assert_updated_at_trigger_passes_with_trigger(owner_conn: Conn) -> None:
    owner_conn.execute(_UPD_TABLE)
    owner_conn.execute(
        "create trigger trg_upd_updated_at before update on public._upd "
        "for each row execute function public.set_updated_at()"
    )
    row = insert_row(owner_conn, "public._upd", name="x", updated_at=SEEDED_UPDATED_AT)

    assert_updated_at_trigger(owner_conn, "public._upd", row["id"], name="y")

    assert _scalar(owner_conn, "select name from public._upd") == "y"


def test_fixture_assert_updated_at_trigger_fails_without_trigger(owner_conn: Conn) -> None:
    owner_conn.execute(_UPD_TABLE)
    row = insert_row(owner_conn, "public._upd", name="x", updated_at=SEEDED_UPDATED_AT)

    with pytest.raises(AssertionError, match="updated_at"):
        assert_updated_at_trigger(owner_conn, "public._upd", row["id"], name="y")


def test_fixture_assert_backend_read_write_round_trip(owner_conn: Conn) -> None:
    owner_conn.execute(
        "create table public._rw(id uuid primary key default gen_random_uuid(), name text)"
    )
    owner_conn.execute("call app_private.secure_table('public._rw')")

    with as_role(owner_conn, "app_backend"):
        row = insert_row(owner_conn, "public._rw", name="x")
        assert_backend_read_write(owner_conn, "public._rw", row, name="y")
        assert _scalar(owner_conn, "select count(*) from public._rw") == 0


def test_fixture_assert_backend_read_write_fails_without_update_privilege(
    owner_conn: Conn,
) -> None:
    owner_conn.execute(
        "create table public._ro(id uuid primary key default gen_random_uuid(), name text)"
    )
    owner_conn.execute("call app_private.secure_table('public._ro', 'select, insert')")

    with as_role(owner_conn, "app_backend"):
        row = insert_row(owner_conn, "public._ro", name="x")
        with pg_error(owner_conn, INSUFFICIENT_PRIVILEGE):
            assert_backend_read_write(owner_conn, "public._ro", row, name="y")
