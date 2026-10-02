"""BACKEND-070：Permission enum（domain_spec §3 全部 28 碼）、分組與說明。"""

from app.core.permissions import (
    ALL_PERMISSIONS,
    PERMISSION_GROUPS,
    PERMISSION_LABELS,
    WILDCARD,
    Permission,
    PermissionGroup,
    is_valid_permission,
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
