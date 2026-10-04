"""BACKEND-074：app/services/rbac_guards.py（assert_valid_permission_codes、assert_can_grant，
自我提權防護）。"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.api.deps import CurrentStaff
from app.core.errors import AppError
from app.core.permissions import ALL_PERMISSIONS
from app.services.rbac_guards import assert_can_grant, assert_valid_permission_codes


def _actor(permissions: frozenset[str]) -> CurrentStaff:
    return CurrentStaff(
        id=uuid4(),
        username="amy",
        display_name="林老師",
        role_id=uuid4(),
        role_code="director",
        role_name="主任",
        permissions=permissions,
        must_change_password=False,
        token_version=0,
    )


def _assert_unknown(exc: AppError, invalid: list[str]) -> None:
    assert exc.status == 422
    assert exc.code == "unknown_permission"
    assert exc.message == "包含無效的權限碼"
    assert exc.details == {"invalid": invalid}


def test_rbac_valid_codes() -> None:
    assert assert_valid_permission_codes(["students:read", "classes:read", "students:read"]) == [
        "classes:read",
        "students:read",
    ]
    assert assert_valid_permission_codes([]) == []
    assert assert_valid_permission_codes(iter(("pickup:read",))) == ["pickup:read"]

    with pytest.raises(AppError) as exc:
        assert_valid_permission_codes(["*"])
    _assert_unknown(exc.value, ["*"])

    with pytest.raises(AppError) as exc:
        assert_valid_permission_codes(["x:y"])
    _assert_unknown(exc.value, ["x:y"])

    # 合法與不合法混合：只列出不合法的，去重排序
    with pytest.raises(AppError) as exc:
        assert_valid_permission_codes(["students:read", "z:z", "*", "z:z", "Students:Read"])
    _assert_unknown(exc.value, ["*", "Students:Read", "z:z"])


def test_rbac_can_grant_subset() -> None:
    actor = _actor(frozenset({"students:read", "students:write", "roles:write"}))

    assert assert_can_grant(actor, ["students:read"]) is None
    assert assert_can_grant(actor, ["students:read", "students:write", "roles:write"]) is None
    assert assert_can_grant(actor, []) is None


def test_rbac_cannot_grant_beyond() -> None:
    actor = _actor(frozenset({"students:read", "students:write", "roles:write"}))

    with pytest.raises(AppError) as exc:
        assert_can_grant(actor, ["students:read", "pickup:override"])

    assert exc.value.status == 403
    assert exc.value.code == "cannot_grant_permissions"
    assert exc.value.details == {"permissions": ["pickup:override"]}

    with pytest.raises(AppError) as exc:
        assert_can_grant(actor, ["exams:write", "pickup:override", "exams:write"])
    assert exc.value.details == {"permissions": ["exams:write", "pickup:override"]}


def test_rbac_can_grant_admin_all() -> None:
    """admin 角色（有效權限 == ALL_PERMISSIONS）授出任何合法碼都通過。"""
    admin = _actor(ALL_PERMISSIONS)

    assert assert_can_grant(admin, sorted(ALL_PERMISSIONS)) is None
    assert assert_can_grant(_actor(frozenset()), []) is None
    with pytest.raises(AppError) as exc:
        assert_can_grant(_actor(frozenset()), ["students:read"])
    assert exc.value.code == "cannot_grant_permissions"
