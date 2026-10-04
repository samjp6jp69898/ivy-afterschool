"""BACKEND-076：角色 schemas。"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.roles import (
    PermissionCatalogOut,
    PermissionGroupOut,
    PermissionItemOut,
    RoleCreateIn,
    RoleOut,
    RoleUpdateIn,
)


def test_role_schemas_code_format() -> None:
    with pytest.raises(ValidationError):
        RoleCreateIn(code="Front Desk", name="櫃台", permissions=[])
    assert RoleCreateIn(code="front_desk", name="櫃台", permissions=[]).code == "front_desk"


@pytest.mark.parametrize("code", ["a", "1abc", "a" * 33, "ab-c"])
def test_role_schemas_code_format_rejects_boundaries(code: str) -> None:
    with pytest.raises(ValidationError):
        RoleCreateIn(code=code, name="櫃台", permissions=[])


def test_role_schemas_code_format_accepts_length_boundaries() -> None:
    assert RoleCreateIn(code="ab", name="x", permissions=[]).code == "ab"
    assert RoleCreateIn(code="a" * 32, name="x", permissions=[]).code == "a" * 32


def test_role_schemas_update_requires_field() -> None:
    with pytest.raises(ValidationError, match="至少要修改一個欄位"):
        RoleUpdateIn.model_validate({})
    with pytest.raises(ValidationError) as exc:
        RoleUpdateIn.model_validate({"code": "x"})
    assert "extra_forbidden" in {e["type"] for e in exc.value.errors()}
    assert RoleUpdateIn.model_validate({"permissions": []}).permissions == []


def test_role_schemas_name_length() -> None:
    with pytest.raises(ValidationError):
        RoleCreateIn(code="front_desk", name="", permissions=[])
    with pytest.raises(ValidationError):
        RoleCreateIn(code="front_desk", name="櫃" * 51, permissions=[])
    assert RoleCreateIn(code="front_desk", name="櫃" * 50, permissions=[]).name == "櫃" * 50
    with pytest.raises(ValidationError):
        RoleUpdateIn.model_validate({"name": "   "})


def test_role_schemas_description_and_permissions_limits() -> None:
    assert RoleCreateIn(code="front_desk", name="櫃台", permissions=[]).description is None
    with pytest.raises(ValidationError):
        RoleCreateIn(code="front_desk", name="櫃台", description="a" * 201, permissions=[])
    with pytest.raises(ValidationError):
        RoleCreateIn(code="front_desk", name="櫃台", permissions=[f"p.{i}" for i in range(51)])
    ok = RoleCreateIn(code="front_desk", name="櫃台", permissions=[f"p.{i}" for i in range(50)])
    assert len(ok.permissions) == 50


def test_role_schemas_create_rejects_extra() -> None:
    with pytest.raises(ValidationError) as exc:
        RoleCreateIn.model_validate(
            {"code": "front_desk", "name": "櫃台", "permissions": [], "is_system": True}
        )
    assert exc.value.errors()[0]["type"] == "extra_forbidden"


def test_role_schemas_out_from_orm_with_computed_values() -> None:
    orm_like = SimpleNamespace(
        id=uuid4(),
        code="admin",
        name="管理員",
        description=None,
        is_system=True,
        permissions=["*"],
        effective_permissions=["attendance.read"],
        staff_count=2,
        created_at=datetime(2026, 9, 1, tzinfo=UTC),
        updated_at=datetime(2026, 9, 2, tzinfo=UTC),
    )
    out = RoleOut.model_validate(orm_like)
    assert out.permissions == ["*"]
    assert out.effective_permissions == ["attendance.read"]
    assert out.staff_count == 2


def test_role_schemas_catalog_shape() -> None:
    catalog = PermissionCatalogOut(
        groups=[
            PermissionGroupOut(
                key="attendance",
                label="出勤",
                permissions=[PermissionItemOut(code="attendance.read", label="查看出勤")],
            )
        ]
    )
    assert catalog.model_dump() == {
        "groups": [
            {
                "key": "attendance",
                "label": "出勤",
                "permissions": [{"code": "attendance.read", "label": "查看出勤"}],
            }
        ]
    }


def test_role_schemas_rejects_nul_in_permissions_and_name() -> None:
    with pytest.raises(ValidationError, match="NUL"):
        RoleCreateIn(code="front_desk", name="櫃台", permissions=["a.read\x00"])
    with pytest.raises(ValidationError, match="NUL"):
        RoleUpdateIn.model_validate({"name": "櫃\x00台"})
