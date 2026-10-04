"""BACKEND-039：認證模組 schemas（員工登入 / 改密碼 / me，家長 LIFF / 綁定）。"""

# ruff: noqa: S105, S106  測試用的密碼 / token 字面值

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.auth import (
    BindIn,
    ChangePasswordIn,
    LiffLoginIn,
    LiffLoginOut,
    MessageOut,
    ParentAuthOut,
    RoleBrief,
    StaffAuthOut,
    StaffLoginIn,
    StaffMeOut,
)
from app.schemas.parent_children import ParentMeOut


def _error_types(exc: ValidationError) -> set[str]:
    return {e["type"] for e in exc.errors()}


def test_auth_schemas_extra_forbid() -> None:
    with pytest.raises(ValidationError) as exc:
        StaffLoginIn.model_validate({"username": "amy", "password": "x", "remember": True})
    assert "extra_forbidden" in _error_types(exc.value)

    for model, payload in (
        (ChangePasswordIn, {"current_password": "a", "new_password": "b", "confirm": "b"}),
        (LiffLoginIn, {"id_token": "t", "nonce": "n"}),
        (BindIn, {"code": "ABCD-1234", "student_id": "x"}),
    ):
        with pytest.raises(ValidationError) as exc:
            model.model_validate(payload)
        assert "extra_forbidden" in _error_types(exc.value), model.__name__


def test_auth_schemas_strip() -> None:
    staff_login = StaffLoginIn(username="  amy  ", password=" p ")
    assert staff_login.username == "amy"
    assert staff_login.password == " p "

    change = ChangePasswordIn(current_password=" old1 ", new_password=" new2 ")
    assert change.current_password == " old1 "
    assert change.new_password == " new2 "

    assert LiffLoginIn(id_token="  tok  ").id_token == "tok"
    # 綁定碼允許含連字號與空白（正規化在 service），只去頭尾空白
    assert BindIn(code="  AB CD-12 ").code == "AB CD-12"


def test_auth_schemas_length() -> None:
    with pytest.raises(ValidationError):
        StaffLoginIn(username="a" * 33, password="x")
    assert StaffLoginIn(username="a" * 32, password="x").username == "a" * 32
    with pytest.raises(ValidationError):
        StaffLoginIn(username="", password="x")
    with pytest.raises(ValidationError):
        StaffLoginIn(username="amy", password="")
    with pytest.raises(ValidationError):
        StaffLoginIn(username="amy", password="p" * 129)
    assert len(StaffLoginIn(username="amy", password="p" * 128).password) == 128

    with pytest.raises(ValidationError):
        ChangePasswordIn(current_password="", new_password="x")
    with pytest.raises(ValidationError):
        ChangePasswordIn(current_password="x", new_password="n" * 129)

    with pytest.raises(ValidationError):
        LiffLoginIn(id_token="")
    with pytest.raises(ValidationError):
        LiffLoginIn(id_token="t" * 4097)
    assert len(LiffLoginIn(id_token="t" * 4096).id_token) == 4096

    with pytest.raises(ValidationError):
        BindIn(code="ABC")
    with pytest.raises(ValidationError):
        BindIn(code="A" * 21)
    assert BindIn(code="ABCD").code == "ABCD"
    assert BindIn(code="A" * 20).code == "A" * 20


def test_auth_schemas_me_sorted_permissions() -> None:
    role = RoleBrief(id=uuid4(), code="tutor", name="課輔老師")
    me = StaffMeOut(
        id=uuid4(),
        username="amy",
        display_name="林老師",
        role=role,
        permissions=sorted({"b:x", "a:y"}),
        must_change_password=False,
    )

    dumped = me.model_dump()
    assert dumped["permissions"] == ["a:y", "b:x"]
    assert dumped["role"] == {"id": role.id, "code": "tutor", "name": "課輔老師"}
    assert StaffAuthOut(user=me).model_dump()["user"]["username"] == "amy"


def test_auth_schemas_me_from_orm_like_objects() -> None:
    """StaffMeOut / RoleBrief 可由 ORM 物件（屬性存取）建構。"""
    role_id, staff_id = uuid4(), uuid4()
    role = SimpleNamespace(id=role_id, code="clerk", name="行政")
    staff = SimpleNamespace(
        id=staff_id,
        username="amy",
        display_name="林老師",
        role=role,
        permissions=["students:read", "classes:read"],
        must_change_password=True,
    )

    me = StaffMeOut.model_validate(staff)

    assert me.id == staff_id
    assert me.role == RoleBrief(id=role_id, code="clerk", name="行政")
    assert me.must_change_password is True


def test_auth_schemas_liff_login_out_and_message() -> None:
    needs_binding = LiffLoginOut(status="needs_binding", parent=None, name_hint="王媽媽")
    assert needs_binding.model_dump() == {
        "status": "needs_binding",
        "parent": None,
        "name_hint": "王媽媽",
    }
    with pytest.raises(ValidationError):
        LiffLoginOut(status="pending", parent=None, name_hint=None)

    parent = ParentMeOut(
        id=uuid4(), display_name="王媽媽", picture_url=None, phone=None, children=[]
    )
    ok = LiffLoginOut(status="ok", parent=parent, name_hint=None)
    assert ok.model_dump()["parent"]["display_name"] == "王媽媽"
    assert ParentAuthOut(parent=parent).parent.id == parent.id
    assert MessageOut(message="已登出").model_dump() == {"message": "已登出"}
