"""DB 整合測試共用 fixture（DB-002）：psycopg 層級的連線、角色切換、SQLSTATE 斷言。

給 migration / 約束 / RLS 測試使用；SQLAlchemy `db_session` 由 INFRA-010 的上層 conftest 提供。
helper 以 `from tests.integration.db.conftest import ...` 取用。

連線原則：測資一律經 `backend_conn`（app_backend 實際登入）寫入；app_backend 登入失敗就中止整個
測試，絕不改用 owner。`owner_conn` 只限 DB-002 description 列出的五種場合（角色被拒驗證、測試內
DDL、Supabase 管理的 schema、真 commit 後清表、查 app_backend 讀不到的 pg_catalog 資訊）。

兩種連線都只連本機 loopback：連線前以 `db_urls` 依 libpq 規則檢查 URL 與環境變數，連線後再以
`conn.info.hostaddr` 複驗，違規一律 `pytest.exit`。每個測試在 transaction 內執行，結束一律
rollback。
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg
import pytest
from psycopg import sql
from psycopg.rows import dict_row

from tests.integration.db.factories import split_table
from tests.support import db_urls

Conn = psycopg.Connection[tuple[Any, ...]]

UNIQUE_VIOLATION = "23505"
CHECK_VIOLATION = "23514"
FK_VIOLATION = "23503"
NOT_NULL_VIOLATION = "23502"
INSUFFICIENT_PRIVILEGE = "42501"
EXCLUSION_VIOLATION = "23P01"
RAISE_EXCEPTION = "P0001"

# 各表 trigger 測試插入時指定的 updated_at，update 後必須被 trigger 改成 now()
SEEDED_UPDATED_AT = datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=8)))

# app_backend 只用於 owner transaction 內驗證臨時物件（DDL 情境）
_SWITCHABLE_ROLES = frozenset({"anon", "authenticated", "service_role", "app_backend"})
_EXIT_CODE = 2


def _connect(url_getter: Callable[[], str], on_login_failure: str) -> Conn:
    try:
        url = url_getter()
    except ValueError as exc:
        pytest.exit(str(exc), returncode=_EXIT_CODE)
    try:
        conn = psycopg.connect(db_urls.to_psycopg_dsn(url))
    except psycopg.OperationalError as exc:
        pytest.exit(f"{on_login_failure}（{exc}）", returncode=_EXIT_CODE)
    try:
        db_urls.assert_connected_loopback(conn.info.hostaddr)
    except ValueError as exc:
        conn.close()
        pytest.exit(str(exc), returncode=_EXIT_CODE)
    return conn


def connect_backend() -> Conn:
    """以 app_backend 實際登入本機 DB；失敗時中止測試，不會改用 owner。"""
    return _connect(
        db_urls.backend_url,
        "app_backend 無法登入，先跑 just db-start 與 just db-reset --yes",
    )


def connect_owner() -> Conn:
    """以 owner（本機 postgres）連線；只限 module docstring 列出的場合。"""
    return _connect(
        db_urls.owner_url,
        "本機 Supabase 的 owner 連線失敗，先跑 just db-start 與 just db-reset --yes",
    )


@contextmanager
def _rollback_on_exit(conn: Conn) -> Iterator[Conn]:
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


@pytest.fixture
def backend_conn() -> Iterator[Conn]:
    with _rollback_on_exit(connect_backend()) as conn:
        yield conn


@pytest.fixture
def owner_conn() -> Iterator[Conn]:
    with _rollback_on_exit(connect_owner()) as conn:
        yield conn


@contextmanager
def as_role(conn: Conn, role: str) -> Iterator[None]:
    """在目前 transaction 內 `set local role <role>`，離開時 `reset role`。"""
    if role not in _SWITCHABLE_ROLES:
        raise ValueError(f"as_role 只接受 {sorted(_SWITCHABLE_ROLES)}，收到 {role!r}")
    conn.execute(sql.SQL("set local role {}").format(sql.Identifier(role)))
    try:
        yield
    finally:
        # transaction 已失敗時 reset 會再拋錯；set local 會隨 rollback 一起失效
        if conn.info.transaction_status != psycopg.pq.TransactionStatus.INERROR:
            conn.execute("reset role")


@dataclass
class PgErrorInfo:
    """`pg_error` 捕捉到的例外；區塊結束後才有值。"""

    error: psycopg.Error | None = None

    @property
    def constraint_name(self) -> str | None:
        return None if self.error is None else self.error.diag.constraint_name


@contextmanager
def pg_error(conn: Conn, sqlstate: str) -> Iterator[PgErrorInfo]:
    """斷言區塊內拋出 SQLSTATE 為 `sqlstate` 的 DB 錯誤；之後 rollback 到 savepoint 讓測試繼續。"""
    info = PgErrorInfo()
    conn.execute("savepoint pg_error")
    try:
        yield info
    except psycopg.Error as exc:
        conn.execute("rollback to savepoint pg_error")
        info.error = raised = exc
    else:
        conn.execute("release savepoint pg_error")
        raise AssertionError(f"預期 SQLSTATE {sqlstate}，但區塊內沒有拋出 DB 錯誤")
    if raised.sqlstate != sqlstate:
        raise AssertionError(f"預期 SQLSTATE {sqlstate}，實際 {raised.sqlstate}：{raised}")


def assert_table_secured(owner_conn: Conn, table: str) -> None:
    """斷言表已套用 secure_table。

    RLS 與 force RLS 皆開啟、`app_backend_all` policy 存在、anon / authenticated select 得到 42501。
    """
    schema, name = split_table(table)
    row = owner_conn.execute(
        """
        select c.relrowsecurity, c.relforcerowsecurity
        from pg_class c join pg_namespace n on n.oid = c.relnamespace
        where n.nspname = %s and c.relname = %s
        """,
        (schema, name),
    ).fetchone()
    assert row is not None, f"找不到表 {schema}.{name}"
    assert row == (True, True), f"{schema}.{name} 的 (relrowsecurity, relforcerowsecurity) = {row}"
    policy = owner_conn.execute(
        """
        select 1 from pg_policies
        where schemaname = %s and tablename = %s and policyname = 'app_backend_all'
        """,
        (schema, name),
    ).fetchone()
    assert policy is not None, f"{schema}.{name} 沒有 app_backend_all policy"
    select = sql.SQL("select * from {}").format(sql.Identifier(schema, name))
    for role in ("anon", "authenticated"):
        with as_role(owner_conn, role), pg_error(owner_conn, INSUFFICIENT_PRIVILEGE):
            owner_conn.execute(select)


def _set_clause(changes: dict[str, Any]) -> sql.Composable:
    assert changes, "至少要指定一個要 update 的欄位"
    return sql.SQL(", ").join(
        sql.SQL("{} = {}").format(sql.Identifier(col), sql.Placeholder()) for col in changes
    )


def assert_updated_at_trigger(conn: Conn, table: str, row_id: Any, **changes: Any) -> None:
    """列的 updated_at 原為 SEEDED_UPDATED_AT；update `changes` 後須等於 transaction 的 now()。"""
    target = sql.Identifier(*split_table(table))
    before = conn.execute(
        sql.SQL("select updated_at from {} where id = %s").format(target), (row_id,)
    ).fetchone()
    assert before == (SEEDED_UPDATED_AT,), (
        f"{table} 插入時的 updated_at 應為 {SEEDED_UPDATED_AT}：{before}"
    )
    after = conn.execute(
        sql.SQL("update {} set {} where id = {} returning updated_at, now()").format(
            target, _set_clause(changes), sql.Placeholder()
        ),
        [*changes.values(), row_id],
    ).fetchone()
    assert after is not None, f"{table} 找不到 id={row_id}"
    updated_at, tx_now = after
    assert updated_at == tx_now, f"{table} update 後 updated_at={updated_at}，應為 now()={tx_now}"


def assert_backend_read_write(conn: Conn, table: str, row: dict[str, Any], **changes: Any) -> None:
    """以 `row['id']` select 回同一列、update `changes` 後回傳值相符、delete 後查無此列。"""
    target = sql.Identifier(*split_table(table))
    by_id = sql.SQL("select * from {} where id = %s").format(target)
    with conn.cursor(row_factory=dict_row) as cur:
        assert cur.execute(by_id, (row["id"],)).fetchone() == row, f"{table} 讀不回剛插入的列"
        updated = cur.execute(
            sql.SQL("update {} set {} where id = {} returning *").format(
                target, _set_clause(changes), sql.Placeholder()
            ),
            [*changes.values(), row["id"]],
        ).fetchone()
        assert updated is not None, f"{table} update 沒有影響任何列"
        assert {col: updated[col] for col in changes} == changes
        cur.execute(sql.SQL("delete from {} where id = %s").format(target), (row["id"],))
        assert cur.rowcount == 1, f"{table} delete 影響 {cur.rowcount} 列"
        assert cur.execute(by_id, (row["id"],)).fetchone() is None
