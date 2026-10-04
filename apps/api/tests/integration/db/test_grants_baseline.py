"""DB-040：grant 收尾 revision db040（RLS、ACL、function EXECUTE、實際登入存取、fail-closed）。

目錄層級的斷言要看到其他角色的權限與 ACL，屬 DB-002 允許 `owner_conn` 的場合；fail-closed 反例
在 owner transaction 內建一張只違反一條規則的臨時表，再執行 db040 的 VERIFY_SQL，結束 rollback。
實際存取一律以 `backend_conn`（app_backend 登入）驗證；表清單從 pg_class 取（所有角色都看得到），
不用 information_schema（只列出目前角色有權限的表，會讓迴圈恆真）。
"""

from typing import Any

from psycopg import sql

from tests.integration.db.conftest import (
    INSUFFICIENT_PRIVILEGE,
    RAISE_EXCEPTION,
    Conn,
    load_revision_sql,
    pg_error,
)

_REVISION_FILE = "db040_enforce_grants_baseline.py"
_ALL_TABLE_PRIVILEGES = "select, insert, update, delete, truncate, references, trigger"
_PUBLIC_TABLES_SQL = """
    select c.relname
    from pg_class c
    join pg_namespace n on n.oid = c.relnamespace
    where n.nspname = 'public' and c.relkind in ('r', 'p')
    order by c.relname
"""


def _public_tables(conn: Conn) -> list[str]:
    return [name for (name,) in conn.execute(_PUBLIC_TABLES_SQL).fetchall()]


def _one(conn: Conn, query: str, params: tuple[Any, ...] = ()) -> tuple[Any, ...]:
    row = conn.execute(query, params).fetchone()
    assert row is not None, f"查詢沒有回傳任何列：{query}"
    return row


def _verify_sql() -> str:
    return load_revision_sql(_REVISION_FILE, "VERIFY_SQL")


def test_grants_baseline_no_rls(owner_conn: Conn) -> None:
    rls_tables = owner_conn.execute(
        """
        select c.relname
        from pg_class c
        join pg_namespace n on n.oid = c.relnamespace
        where n.nspname = 'public' and c.relkind in ('r', 'p')
          and (c.relrowsecurity or c.relforcerowsecurity)
        order by c.relname
        """
    ).fetchall()
    assert rls_tables == []
    assert _one(owner_conn, "select count(*) from pg_policies where schemaname = 'public'") == (0,)


def test_grants_baseline_acl_only_owner_and_backend(owner_conn: Conn) -> None:
    tables = _public_tables(owner_conn)
    assert {"alembic_version", "students", "audit_logs"} <= set(tables)

    foreign_grantees = owner_conn.execute(
        """
        select c.relname, coalesce(r.rolname, 'PUBLIC')
        from pg_class c
        join pg_namespace n on n.oid = c.relnamespace
        cross join lateral aclexplode(c.relacl) a
        left join pg_roles r on r.oid = a.grantee
        where n.nspname = 'public' and c.relkind in ('r', 'p')
          and a.grantee <> c.relowner
          and coalesce(r.rolname, 'PUBLIC') <> 'app_backend'
        order by 1, 2
        """
    ).fetchall()
    assert foreign_grantees == []

    public_access = [
        table
        for table in tables
        if _one(
            owner_conn,
            "select has_table_privilege("
            "'public', to_regclass(format('%%I.%%I', 'public', %s::text)), %s::text)",
            (table, _ALL_TABLE_PRIVILEGES),
        )
        == (True,)
    ]
    assert public_access == []


def test_grants_baseline_public_has_no_function_execute(owner_conn: Conn) -> None:
    routines = owner_conn.execute(
        """
        select n.nspname, p.proname, has_function_privilege('public', p.oid, 'execute')
        from pg_proc p
        join pg_namespace n on n.oid = p.pronamespace
        where n.nspname in ('public', 'app_private')
        order by 1, 2
        """
    ).fetchall()
    names = {(schema, name) for schema, name, _ in routines}
    assert {("public", "set_updated_at"), ("app_private", "grant_backend")} <= names

    public_executable = [(schema, name) for schema, name, granted in routines if granted]
    assert public_executable == []


def test_grants_baseline_real_login_backend_access(backend_conn: Conn) -> None:
    tables = [table for table in _public_tables(backend_conn) if table != "alembic_version"]
    assert {"students", "audit_logs", "roles"} <= set(tables)

    for table in tables:
        backend_conn.execute(
            sql.SQL("select 1 from {} limit 1").format(sql.Identifier("public", table))
        ).fetchall()
    # 本機 DB 由多個 agent 共用，只斷言 seed 的系統角色（其他測試可能留下非系統角色）
    system_roles = backend_conn.execute(
        "select code from public.roles where is_system order by code"
    ).fetchall()
    assert system_roles == [("admin",), ("clerk",), ("director",), ("tutor",)]

    with pg_error(backend_conn, INSUFFICIENT_PRIVILEGE):
        backend_conn.execute("update public.audit_logs set action = 'x.y'")
    with pg_error(backend_conn, INSUFFICIENT_PRIVILEGE):
        backend_conn.execute("select * from public.alembic_version")


def test_grants_baseline_verify_sql_passes_on_current_schema(owner_conn: Conn) -> None:
    owner_conn.execute(_verify_sql())


def test_grants_baseline_fail_closed_on_public_grant(owner_conn: Conn) -> None:
    verify_sql = _verify_sql()
    owner_conn.execute("create table public._leak(id int)")
    owner_conn.execute("grant all on public._leak to app_backend")
    owner_conn.execute("grant select on public._leak to public")

    with pg_error(owner_conn, RAISE_EXCEPTION) as err:
        owner_conn.execute(verify_sql)
    assert "_leak" in str(err.error)


def test_grants_baseline_fail_closed_on_ungranted_table(owner_conn: Conn) -> None:
    verify_sql = _verify_sql()
    owner_conn.execute("create table public._orphan(id int)")

    with pg_error(owner_conn, RAISE_EXCEPTION) as err:
        owner_conn.execute(verify_sql)
    assert "_orphan" in str(err.error)


def test_grants_baseline_fail_closed_on_rls(owner_conn: Conn) -> None:
    verify_sql = _verify_sql()
    owner_conn.execute("create table public._rls(id int)")
    owner_conn.execute("call app_private.grant_backend('public._rls')")
    owner_conn.execute("alter table public._rls enable row level security")

    with pg_error(owner_conn, RAISE_EXCEPTION) as err:
        owner_conn.execute(verify_sql)
    assert "_rls" in str(err.error)


def test_grants_baseline_fail_closed_on_alembic_version_grant(owner_conn: Conn) -> None:
    verify_sql = _verify_sql()
    owner_conn.execute("grant select on public.alembic_version to app_backend")

    with pg_error(owner_conn, RAISE_EXCEPTION) as err:
        owner_conn.execute(verify_sql)
    assert "alembic_version" in str(err.error)
