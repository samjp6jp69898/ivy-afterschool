"""DB-041：baseline revision db001。

涵蓋 extensions、app_private、set_updated_at、app_backend、grant_backend 與 function 預設權限。

驗證 revision 本身需要在測試內建臨時表與 function（DDL）、在同一 owner transaction 內切換到
app_backend 觀察授權結果，並查其他角色的權限，屬 DB-002 連線原則允許使用 `owner_conn` 的場合。
每個測試結束一律 rollback。
"""

from pathlib import Path
from typing import Any

import pytest

from tests.integration.db.conftest import (
    INSUFFICIENT_PRIVILEGE,
    RAISE_EXCEPTION,
    SEEDED_UPDATED_AT,
    Conn,
    as_role,
    assert_updated_at_trigger,
    pg_error,
)
from tests.integration.db.factories import insert_row

_REVISION_PATH = Path(__file__).resolve().parents[3] / "alembic" / "versions" / "db001_base.py"
_PROBE_TABLE = "create table public._t(id uuid primary key default gen_random_uuid(), name text)"


def _one(conn: Conn, sql: str, params: tuple[Any, ...] = ()) -> tuple[Any, ...]:
    row = conn.execute(sql, params).fetchone()
    assert row is not None, f"查詢沒有回傳任何列：{sql}"
    return row


def _acl(conn: Conn, table: str) -> dict[str, set[str]]:
    """表的 ACL：grantee（PUBLIC 以 'PUBLIC' 表示）→ privilege_type 集合。"""
    acl: dict[str, set[str]] = {}
    rows = conn.execute(
        """
        select coalesce(r.rolname, 'PUBLIC'), a.privilege_type
        from pg_class c
        cross join lateral aclexplode(c.relacl) a
        left join pg_roles r on r.oid = a.grantee
        where c.oid = %s::regclass
        """,
        (table,),
    ).fetchall()
    for grantee, privilege in rows:
        acl.setdefault(grantee, set()).add(privilege)
    return acl


def test_base_pgcrypto_in_extensions_schema(owner_conn: Conn) -> None:
    rows = owner_conn.execute(
        """
        select n.nspname
        from pg_extension e
        join pg_namespace n on n.oid = e.extnamespace
        where e.extname = 'pgcrypto'
        """
    ).fetchall()
    assert rows == [("extensions",)]


def test_base_app_backend_role_attributes(owner_conn: Conn) -> None:
    rows = owner_conn.execute(
        """
        select rolsuper, rolbypassrls, rolcreaterole, rolcreatedb, rolinherit
        from pg_roles
        where rolname = 'app_backend'
        """
    ).fetchall()
    assert rows == [(False, False, False, False, False)]


def test_base_grant_backend_on_temp_table(owner_conn: Conn) -> None:
    owner_conn.execute(_PROBE_TABLE)
    owner_conn.execute("call app_private.grant_backend('public._t')")

    acl = _acl(owner_conn, "public._t")
    assert set(acl) == {"postgres", "app_backend"}
    assert acl["app_backend"] == {"SELECT", "INSERT", "UPDATE", "DELETE"}
    assert _one(owner_conn, "select has_table_privilege('public', 'public._t', 'select')") == (
        False,
    )

    with as_role(owner_conn, "app_backend"):
        row = insert_row(owner_conn, "public._t", name="x")
        owner_conn.execute("update public._t set name = 'y' where id = %s", (row["id"],))
        assert _one(owner_conn, "select name from public._t") == ("y",)
        owner_conn.execute("delete from public._t where id = %s", (row["id"],))
        assert _one(owner_conn, "select count(*) from public._t") == (0,)


def test_base_grant_backend_append_only(owner_conn: Conn) -> None:
    owner_conn.execute(_PROBE_TABLE)
    owner_conn.execute("call app_private.grant_backend('public._t', 'select, insert')")

    assert _acl(owner_conn, "public._t")["app_backend"] == {"SELECT", "INSERT"}
    with as_role(owner_conn, "app_backend"):
        insert_row(owner_conn, "public._t", name="x")
        with pg_error(owner_conn, INSUFFICIENT_PRIVILEGE):
            owner_conn.execute("update public._t set name = 'y'")


@pytest.mark.parametrize(
    "bad_privileges",
    ["select; drop table x", "truncate", "", "select, references"],
)
def test_base_grant_backend_rejects_bad_privileges(owner_conn: Conn, bad_privileges: str) -> None:
    owner_conn.execute(_PROBE_TABLE)
    (acl_before,) = _one(
        owner_conn, "select relacl::text from pg_class where oid = 'public._t'::regclass"
    )

    with pg_error(owner_conn, RAISE_EXCEPTION):
        owner_conn.execute("call app_private.grant_backend('public._t', %s)", (bad_privileges,))

    assert _one(owner_conn, "select to_regclass('public._t')::text") == ("_t",)
    assert _one(
        owner_conn, "select relacl::text from pg_class where oid = 'public._t'::regclass"
    ) == (acl_before,)


def test_base_set_updated_at_trigger(owner_conn: Conn) -> None:
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
        create trigger trg_t3_updated_at before update on public._t3
        for each row execute function public.set_updated_at()
        """
    )
    row = insert_row(owner_conn, "public._t3", name="x", updated_at=SEEDED_UPDATED_AT)

    assert_updated_at_trigger(owner_conn, "public._t3", row["id"], name="y")


def test_base_public_has_no_access_to_private_objects(owner_conn: Conn) -> None:
    assert _one(
        owner_conn,
        """
        select has_schema_privilege('public', 'app_private', 'usage'),
               has_function_privilege('public', 'public.set_updated_at()', 'execute'),
               has_function_privilege(
                   'public', 'app_private.grant_backend(regclass,text)', 'execute'
               )
        """,
    ) == (False, False, False)


def test_base_default_privileges_revoke_function_execute(owner_conn: Conn) -> None:
    owner_conn.execute("create function public._f() returns int language sql as 'select 1'")

    assert _one(
        owner_conn,
        """
        select has_function_privilege('public', 'public._f()', 'execute'),
               has_function_privilege('app_backend', 'public._f()', 'execute')
        """,
    ) == (False, False)


def test_base_no_rls_no_vendor_roles(owner_conn: Conn) -> None:
    assert _one(
        owner_conn,
        """
        select count(*) from pg_class c join pg_namespace n on n.oid = c.relnamespace
        where n.nspname = 'public' and c.relrowsecurity
        """,
    ) == (0,)
    assert _one(owner_conn, "select count(*) from pg_policies where schemaname = 'public'") == (0,)

    text = _REVISION_PATH.read_text(encoding="utf-8").lower()
    for forbidden in ("anon", "authenticated", "service_role", "row level security"):
        assert forbidden not in text, forbidden


def test_base_alembic_version_not_granted(owner_conn: Conn) -> None:
    assert _one(
        owner_conn, "select has_table_privilege('app_backend', 'public.alembic_version', 'select')"
    ) == (False,)
