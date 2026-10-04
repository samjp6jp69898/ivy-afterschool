"""DB-035：系統角色與預設權限 seed（db035 的 SEED_SQL）。

冪等測試需在同一 transaction 內先改測資再以 owner（migration 的身分）重跑 SEED_SQL：測資的
update 以 `as_role(owner_conn, 'app_backend')` 寫入，SEED_SQL 本身以 owner 執行。
"""

import importlib.util
from pathlib import Path
from typing import Any

from app.core.permissions import Permission
from tests.integration.db.conftest import Conn, as_role

_REVISION_PATH = (
    Path(__file__).resolve().parents[3] / "alembic" / "versions" / "db035_seed_system_roles.py"
)

DIRECTOR = {
    "dashboard:read",
    "staff:read",
    "roles:read",
    "settings:read",
    "settings:write",
    "audit:read",
    "classes:read",
    "classes:write",
    "students:read",
    "students:write",
    "students:sensitive",
    "guardians:write",
    "attendance:read",
    "attendance:operate",
    "attendance:amend",
    "leaves:read",
    "leaves:write",
    "homework:read",
    "homework:write",
    "pickup:read",
    "pickup:operate",
    "pickup:override",
    "exams:read",
    "exams:write",
    "exams:publish",
}
CLERK = {
    "dashboard:read",
    "settings:read",
    "classes:read",
    "classes:write",
    "students:read",
    "students:write",
    "guardians:write",
    "attendance:read",
    "attendance:operate",
    "leaves:read",
    "leaves:write",
    "homework:read",
    "homework:write",
    "pickup:read",
    "pickup:operate",
    "exams:read",
    "exams:write",
    "exams:publish",
}
TUTOR = {
    "dashboard:read",
    "classes:read",
    "students:read",
    "attendance:read",
    "attendance:operate",
    "leaves:read",
    "homework:read",
    "homework:write",
    "pickup:read",
    "pickup:operate",
    "exams:read",
    "exams:write",
}


def _seed_sql() -> str:
    spec = importlib.util.spec_from_file_location("db035_seed_system_roles", _REVISION_PATH)
    assert spec is not None, f"載入不了 {_REVISION_PATH}"
    assert spec.loader is not None, f"載入不了 {_REVISION_PATH}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    seed_sql: Any = module.SEED_SQL
    assert isinstance(seed_sql, str)
    return seed_sql


def _permissions(conn: Conn, code: str) -> list[str]:
    row = conn.execute("select permissions from public.roles where code = %s", (code,)).fetchone()
    assert row is not None, f"找不到角色 {code}"
    permissions: list[str] = row[0]
    return permissions


def test_seed_system_roles_present(backend_conn: Conn) -> None:
    rows = backend_conn.execute(
        """
        select code, is_system from public.roles
        where code in ('admin', 'director', 'clerk', 'tutor') order by code
        """
    ).fetchall()
    assert rows == [("admin", True), ("clerk", True), ("director", True), ("tutor", True)]


def test_seed_system_roles_names_and_descriptions(backend_conn: Conn) -> None:
    rows = backend_conn.execute(
        """
        select code, name, length(btrim(coalesce(description, ''))) > 0 from public.roles
        where code in ('admin', 'director', 'clerk', 'tutor') order by code
        """
    ).fetchall()
    assert rows == [
        ("admin", "系統管理員", True),
        ("clerk", "行政", True),
        ("director", "主任", True),
        ("tutor", "課輔老師", True),
    ]


def test_seed_system_roles_admin_wildcard(backend_conn: Conn) -> None:
    assert _permissions(backend_conn, "admin") == ["*"]


def test_seed_system_roles_director_permissions(backend_conn: Conn) -> None:
    permissions = _permissions(backend_conn, "director")

    assert len(permissions) == 25
    assert set(permissions) == DIRECTOR
    all_codes = {p.value for p in Permission}
    assert set(permissions) == all_codes - {"staff:write", "roles:write", "students:purge"}


def test_seed_system_roles_clerk_permissions(backend_conn: Conn) -> None:
    permissions = _permissions(backend_conn, "clerk")

    assert len(permissions) == 18
    assert set(permissions) == CLERK


def test_seed_system_roles_tutor_permissions(backend_conn: Conn) -> None:
    permissions = _permissions(backend_conn, "tutor")

    assert len(permissions) == 12
    assert set(permissions) == TUTOR
    forbidden = {
        "students:write",
        "classes:write",
        "leaves:write",
        "settings:read",
        "settings:write",
        "exams:publish",
    }
    assert not set(permissions) & forbidden


def test_seed_system_roles_codes_match_permission_enum(backend_conn: Conn) -> None:
    all_codes = {p.value for p in Permission}
    for code in ("director", "clerk", "tutor"):
        assert set(_permissions(backend_conn, code)) <= all_codes, code


def test_seed_system_roles_purge_admin_only(backend_conn: Conn) -> None:
    rows = backend_conn.execute(
        "select code from public.roles where 'students:purge' = any (permissions)"
    ).fetchall()
    assert rows == []
    # admin 以萬用碼持有 students:purge，不逐碼列出
    rows = backend_conn.execute(
        """
        select code from public.roles
        where 'students:purge' = any (permissions) or '*' = any (permissions)
        """
    ).fetchall()
    assert rows == [("admin",)]


def test_seed_system_roles_idempotent(owner_conn: Conn) -> None:
    with as_role(owner_conn, "app_backend"):
        owner_conn.execute(
            "update public.roles set permissions = '{dashboard:read}' where code = 'tutor'"
        )
    before = owner_conn.execute("select count(*) from public.roles").fetchone()

    owner_conn.execute(_seed_sql())

    assert _permissions(owner_conn, "tutor") == ["dashboard:read"]
    assert owner_conn.execute("select count(*) from public.roles").fetchone() == before
