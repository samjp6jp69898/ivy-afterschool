"""BACKEND-070：Permission enum（domain_spec §3 全部 28 碼）、分組與說明。
BACKEND-072：resolve_effective_permissions。
"""

import logging

import pytest

from app.core.permissions import (
    ALL_PERMISSIONS,
    PERMISSION_GROUPS,
    PERMISSION_LABELS,
    WILDCARD,
    Permission,
    PermissionGroup,
    is_valid_permission,
    resolve_effective_permissions,
)

EXPECTED_CODES = {
    "attendance:amend",
    "roles:read",
    "pickup:override",
    "settings:write",
    "roles:write",
    "classes:read",
    "exams:write",
    "students:read",
    "attendance:read",
    "classes:write",
    "leaves:read",
    "pickup:operate",
    "leaves:write",
    "settings:read",
    "homework:read",
    "students:sensitive",
    "attendance:operate",
    "homework:write",
    "students:write",
    "dashboard:read",
    "guardians:write",
    "exams:publish",
    "exams:read",
    "staff:read",
    "pickup:read",
    "staff:write",
    "audit:read",
    "students:purge",
}


def test_permissions_enum_exact() -> None:
    assert {p.value for p in Permission} == EXPECTED_CODES
    assert len(Permission) == 28
    assert frozenset(EXPECTED_CODES) == ALL_PERMISSIONS
    # StrEnum：成員可直接當字串用（例如與 DB 的 text[] 比對）
    assert str(Permission.PICKUP_OPERATE) == "pickup:operate"
    assert Permission.PICKUP_OPERATE in {"pickup:operate"}
    assert "nfc:manage" not in ALL_PERMISSIONS


def test_permissions_labels_complete() -> None:
    assert set(PERMISSION_LABELS) == set(Permission)
    assert PERMISSION_LABELS[Permission.STUDENTS_SENSITIVE] == "查看與寫入身分證、健康備註"
    assert PERMISSION_LABELS[Permission.STUDENTS_PURGE] == "永久刪除（匿名化）退班學生"
    assert all(label.strip() for label in PERMISSION_LABELS.values())


def test_permissions_groups_partition() -> None:
    flattened = [p for group in PERMISSION_GROUPS for p in group.permissions]

    assert len(flattened) == 28
    assert len(set(flattened)) == 28
    assert set(flattened) == set(Permission)
    assert [group.key for group in PERMISSION_GROUPS] == [
        "dashboard",
        "accounts",
        "settings",
        "students",
        "attendance",
        "leaves",
        "homework",
        "pickup",
        "exams",
    ]
    assert all(isinstance(group, PermissionGroup) and group.label for group in PERMISSION_GROUPS)
    accounts = next(g for g in PERMISSION_GROUPS if g.key == "accounts")
    assert set(accounts.permissions) == {
        Permission.STAFF_READ,
        Permission.STAFF_WRITE,
        Permission.ROLES_READ,
        Permission.ROLES_WRITE,
        Permission.AUDIT_READ,
    }
    students = next(g for g in PERMISSION_GROUPS if g.key == "students")
    assert set(students.permissions) == {
        Permission.CLASSES_READ,
        Permission.CLASSES_WRITE,
        Permission.STUDENTS_READ,
        Permission.STUDENTS_WRITE,
        Permission.STUDENTS_SENSITIVE,
        Permission.STUDENTS_PURGE,
        Permission.GUARDIANS_WRITE,
    }


def test_permissions_is_valid() -> None:
    assert is_valid_permission("pickup:operate") is True
    assert is_valid_permission(WILDCARD) is False
    assert is_valid_permission("*") is False
    assert is_valid_permission("pickup:fly") is False
    assert is_valid_permission("") is False
    assert is_valid_permission("PICKUP:OPERATE") is False


# --- BACKEND-072 ------------------------------------------------------------------------

_LOGGER = "app.core.permissions"


def _warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        r.getMessage() for r in caplog.records if r.name == _LOGGER and r.levelno == logging.WARNING
    ]


def test_resolve_wildcard(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger=_LOGGER)

    result = resolve_effective_permissions(["*"], [], [])

    assert result == ALL_PERMISSIONS
    assert len(result) == 28
    assert WILDCARD not in result
    assert isinstance(result, frozenset)
    # * 在角色權限中是合法寫法，不產生 warning
    assert _warnings(caplog) == []


def test_resolve_wildcard_with_other_codes() -> None:
    assert resolve_effective_permissions(["*", "students:read"], ["exams:read"], []) == (
        ALL_PERMISSIONS
    )


def test_resolve_extra_and_revoked() -> None:
    result = resolve_effective_permissions(
        ["students:read", "classes:read"], ["pickup:override"], ["classes:read"]
    )

    assert result == frozenset({"students:read", "pickup:override"})


def test_resolve_revoked_beats_extra() -> None:
    result = resolve_effective_permissions(["students:read"], ["exams:publish"], ["exams:publish"])

    assert "exams:publish" not in result
    assert result == frozenset({"students:read"})


def test_resolve_revoked_role_permission() -> None:
    result = resolve_effective_permissions(["students:read", "exams:read"], [], ["exams:read"])

    assert result == frozenset({"students:read"})


def test_resolve_wildcard_with_revoked() -> None:
    result = resolve_effective_permissions(["*"], [], ["students:sensitive"])

    assert len(result) == 27
    assert "students:sensitive" not in result
    assert result == ALL_PERMISSIONS - {"students:sensitive"}


def test_resolve_drops_invalid_with_warning(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger=_LOGGER)

    result = resolve_effective_permissions(["students:read", "students:fly"], [], [])

    assert result == frozenset({"students:read"})
    warnings = _warnings(caplog)
    assert len(warnings) == 1
    assert "students:fly" in warnings[0]


def test_resolve_invalid_codes_from_all_sources_one_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger=_LOGGER)

    result = resolve_effective_permissions(
        ["students:read", "Students:Write"], ["exams:fly", "", " pickup:read"], ["pickup:old"]
    )

    assert result == frozenset({"students:read"})
    warnings = _warnings(caplog)
    # 一次呼叫只發一則 warning，列出全部不合法碼
    assert len(warnings) == 1
    for code in ("Students:Write", "exams:fly", "pickup:old", "' pickup:read'"):
        assert code in warnings[0]


def test_resolve_wildcard_only_expands_in_role(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger=_LOGGER)

    # extra 裡的 * 不是合法碼：丟棄，不可藉此提權
    result = resolve_effective_permissions([], ["*"], [])

    assert result == frozenset()
    warnings = _warnings(caplog)
    assert len(warnings) == 1
    assert "'*'" in warnings[0]


def test_resolve_wildcard_in_revoked_is_ignored_with_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger=_LOGGER)

    # revoked 只認合法碼；* 不展開（依公式 base + extra 再扣 revoked），但要留下 warning
    result = resolve_effective_permissions(["students:read"], [], ["*"])

    assert result == frozenset({"students:read"})
    warnings = _warnings(caplog)
    assert len(warnings) == 1
    assert "'*'" in warnings[0]


def test_resolve_empty(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger=_LOGGER)

    assert resolve_effective_permissions([], [], []) == frozenset()
    # 空角色只有 extra 時，只得到 extra（不回退任何預設）
    assert resolve_effective_permissions([], ["homework:read"], []) == frozenset({"homework:read"})
    assert _warnings(caplog) == []


def test_resolve_accepts_any_iterable_and_returns_plain_str() -> None:
    result = resolve_effective_permissions(
        (code for code in ["students:read"]),
        {Permission.EXAMS_READ},
        iter([Permission.STUDENTS_READ]),
    )

    assert result == frozenset({"exams:read"})
    assert all(type(code) is str for code in result)
