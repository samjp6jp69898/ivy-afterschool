"""DB-001：基礎 migration（extensions、set_updated_at、app_backend、secure_table、預設權限）。

這裡驗證的是 migration 本身：需要在測試內建臨時表（DDL）、以 `set local role` 切換到
anon / app_backend 觀察授權結果，並查 pg_catalog 中其他角色的權限。依 DB-002 的連線原則，
這些場合是整合測試唯一允許使用 owner（postgres）連線的情況；DB-002 的共用 fixture 依賴本
task，因此本檔自備最小的 owner 連線，只連本機 loopback，每個測試結束一律 rollback。
"""

import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict

_DEFAULT_OWNER_URL = "postgresql://postgres:postgres@127.0.0.1:54342/postgres"
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_LOOPBACK_ADDRS = frozenset({"127.0.0.1", "::1"})
_PUBLIC_ROLES = ("anon", "authenticated", "service_role")

OwnerConn = psycopg.Connection[tuple[Any, ...]]


def _owner_dsn() -> str:
    """讀 TEST_DB_OWNER_URL（預設本機 postgres），依 libpq 規則拒絕任何非 loopback 目標。"""
    url = os.environ.get("TEST_DB_OWNER_URL", _DEFAULT_OWNER_URL)
    dsn = url.replace("postgresql+psycopg://", "postgresql://", 1)
    try:
        params = conninfo_to_dict(dsn)
    except psycopg.ProgrammingError as exc:
        pytest.exit(f"TEST_DB_OWNER_URL 不是合法的連線字串：{exc}", returncode=2)
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
def owner_conn() -> Iterator[OwnerConn]:
    try:
        conn = psycopg.connect(_owner_dsn())
    except psycopg.OperationalError as exc:
        pytest.exit(
            f"本機 Supabase 連不上（{exc}），先跑 just db-start 與 just db-reset --yes",
            returncode=2,
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


def _one(conn: OwnerConn, sql: str, params: tuple[Any, ...] = ()) -> tuple[Any, ...]:
    row = conn.execute(sql, params).fetchone()
    assert row is not None, f"查詢沒有回傳任何列：{sql}"
    return row


def test_base_pgcrypto_in_extensions_schema(owner_conn: OwnerConn) -> None:
    rows = owner_conn.execute(
        """
        select n.nspname
        from pg_extension e
        join pg_namespace n on n.oid = e.extnamespace
        where e.extname = 'pgcrypto'
        """
    ).fetchall()
    assert rows == [("extensions",)]


def test_base_app_backend_role_attributes(owner_conn: OwnerConn) -> None:
    rows = owner_conn.execute(
        """
        select rolbypassrls, rolsuper, rolcreaterole, rolinherit
        from pg_roles
        where rolname = 'app_backend'
        """
    ).fetchall()
    assert rows == [(False, False, False, False)]
    (public_usage,) = _one(
        owner_conn, "select has_schema_privilege('app_backend', 'public', 'usage')"
    )
    assert public_usage is True


def test_base_secure_table_on_temp_table(owner_conn: OwnerConn) -> None:
    owner_conn.execute(
        "create table public._t(id uuid primary key default gen_random_uuid(), name text)"
    )
    owner_conn.execute("call app_private.secure_table('public._t')")

    assert _one(
        owner_conn,
        """
        select relrowsecurity, relforcerowsecurity
        from pg_class where oid = 'public._t'::regclass
        """,
    ) == (True, True)
    policies = owner_conn.execute(
        """
        select policyname, cmd, roles, qual, with_check
        from pg_policies
        where schemaname = 'public' and tablename = '_t'
        order by policyname
        """
    ).fetchall()
    assert policies == [("app_backend_all", "ALL", ["app_backend"], "true", "true")]

    owner_conn.execute("savepoint as_anon")
    owner_conn.execute("set local role anon")
    with pytest.raises(psycopg.errors.InsufficientPrivilege) as exc_info:
        owner_conn.execute("select * from public._t")
    assert exc_info.value.sqlstate == "42501"
    owner_conn.execute("rollback to savepoint as_anon")
    owner_conn.execute("reset role")

    owner_conn.execute("set local role app_backend")
    assert _one(owner_conn, "select current_user") == ("app_backend",)
    owner_conn.execute("insert into public._t(name) values ('x')")
    assert owner_conn.execute("select name from public._t").fetchall() == [("x",)]
    owner_conn.execute("update public._t set name = 'y' where name = 'x'")
    assert owner_conn.execute("select name from public._t").fetchall() == [("y",)]
    owner_conn.execute("delete from public._t")
    assert _one(owner_conn, "select count(*) from public._t") == (0,)
    owner_conn.execute("reset role")


@pytest.mark.parametrize(
    "bad_privileges",
    ["select; drop table x", "", "select, truncate", "all"],
)
def test_base_secure_table_rejects_bad_privileges(
    owner_conn: OwnerConn, bad_privileges: str
) -> None:
    owner_conn.execute("create table public._t(id uuid primary key default gen_random_uuid())")
    owner_conn.execute("savepoint before_call")
    with pytest.raises(psycopg.errors.RaiseException) as exc_info:
        owner_conn.execute("call app_private.secure_table('public._t', %s)", (bad_privileges,))
    assert exc_info.value.sqlstate == "P0001"
    owner_conn.execute("rollback to savepoint before_call")
    assert _one(owner_conn, "select to_regclass('public._t')::text") == ("_t",)


def test_base_set_updated_at_trigger(owner_conn: OwnerConn) -> None:
    owner_conn.execute(
        """
        create table public._t3(
            id uuid primary key default gen_random_uuid(),
            name text,
            updated_at timestamptz not null default now()
        )
        """
    )
    owner_conn.execute(
        """
        create trigger set_updated_at before update on public._t3
        for each row execute function public.set_updated_at()
        """
    )
    seeded = datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=8)))
    (inserted_at,) = _one(
        owner_conn,
        "insert into public._t3(name, updated_at) values ('x', %s) returning updated_at",
        (seeded,),
    )
    assert inserted_at == seeded
    assert inserted_at.astimezone(UTC) == datetime(2025, 12, 31, 16, tzinfo=UTC)

    (updated_at,) = _one(owner_conn, "update public._t3 set name = 'y' returning updated_at")
    (tx_now,) = _one(owner_conn, "select now()")
    assert updated_at == tx_now
    assert updated_at != seeded


def test_base_anon_has_no_usage_on_app_private(owner_conn: OwnerConn) -> None:
    for role in _PUBLIC_ROLES:
        (schema_usage, fn_execute) = _one(
            owner_conn,
            """
            select has_schema_privilege(%s, 'app_private', 'usage'),
                   has_function_privilege(%s, 'public.set_updated_at()', 'execute')
            """,
            (role, role),
        )
        assert schema_usage is False, role
        assert fn_execute is False, role


def test_base_default_privileges_exclude_public_roles(owner_conn: OwnerConn) -> None:
    owner_conn.execute("create table public._t2(id int)")
    owner_conn.execute("create sequence public._s2")
    owner_conn.execute(
        "create function public._f2() returns int language sql as 'select 1'",
    )
    for role in _PUBLIC_ROLES:
        (table_select, seq_usage, fn_execute) = _one(
            owner_conn,
            """
            select has_table_privilege(%s, 'public._t2', 'select'),
                   has_sequence_privilege(%s, 'public._s2', 'usage'),
                   has_function_privilege(%s, 'public._f2()', 'execute')
            """,
            (role, role, role),
        )
        assert table_select is False, role
        assert seq_usage is False, role
        assert fn_execute is False, role

    offenders = owner_conn.execute(
        """
        select d.defaclobjtype, g.rolname
        from pg_default_acl d
        join pg_roles r on r.oid = d.defaclrole
        cross join lateral aclexplode(d.defaclacl) a
        join pg_roles g on g.oid = a.grantee
        where r.rolname = 'postgres'
          and d.defaclnamespace = 'public'::regnamespace
          and g.rolname in ('anon', 'authenticated', 'service_role')
        order by 1, 2
        """
    ).fetchall()
    assert offenders == []
