"""BACKEND-071：DB-035 系統角色 seed 與 app/core/permissions.py、domain_spec §3 的一致性。

CLERK_SPEC / TUTOR_SPEC 直接抄 domain_spec §3「預設授予」欄
（「全部員工」= director、clerk、tutor）；改 §3 時同步改這兩個常數。
讀取經 app_backend（roles 有 select 權限）。
"""

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.permissions import ALL_PERMISSIONS, WILDCARD, is_valid_permission
from app.models.account import Role

CLERK_SPEC = frozenset(
    {
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
)

TUTOR_SPEC = frozenset(
    {
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
)

# director 不含的碼：員工 / 角色管理，以及只有 admin（以 * 涵蓋）的永久刪除
DIRECTOR_EXCLUDED = frozenset({"staff:write", "roles:write", "students:purge"})


def _system_roles(db_session: Session) -> dict[str, Role]:
    rows = db_session.execute(
        select(Role).where(Role.code.in_(["admin", "director", "clerk", "tutor"]))
    ).scalars()
    roles = {role.code: role for role in rows}
    assert set(roles) == {"admin", "director", "clerk", "tutor"}
    assert all(role.is_system for role in roles.values())
    return roles


def _assert_same(code: str, actual: Iterable[str], expected: frozenset[str]) -> None:
    actual_list = list(actual)
    assert len(actual_list) == len(set(actual_list)), f"{code} 的 permissions 有重複碼"
    missing = sorted(expected - set(actual_list))
    extra = sorted(set(actual_list) - expected)
    assert (missing, extra) == ([], []), f"{code} seed 與規格不一致：(seed 缺, seed 多)"


def test_permission_seed_admin_wildcard(db_session: Session) -> None:
    roles = _system_roles(db_session)

    assert roles["admin"].permissions == [WILDCARD]


def test_permission_seed_all_codes_valid(db_session: Session) -> None:
    roles = _system_roles(db_session)

    invalid = {
        (code, perm)
        for code in ("director", "clerk", "tutor")
        for perm in roles[code].permissions
        if not is_valid_permission(perm)
    }
    assert invalid == set()


def test_permission_seed_director_exact(db_session: Session) -> None:
    roles = _system_roles(db_session)

    expected = ALL_PERMISSIONS - DIRECTOR_EXCLUDED
    # 先比對集合（失敗時列出差異），再鎖數量
    _assert_same("director", roles["director"].permissions, expected)
    assert len(ALL_PERMISSIONS) == 28
    assert len(expected) == 25


def test_permission_seed_clerk_tutor_match_spec(db_session: Session) -> None:
    roles = _system_roles(db_session)

    _assert_same("clerk", roles["clerk"].permissions, CLERK_SPEC)
    _assert_same("tutor", roles["tutor"].permissions, TUTOR_SPEC)
    assert len(CLERK_SPEC) == 18
    assert len(TUTOR_SPEC) == 12
