"""BACKEND-086：員工帳號模組 schemas。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.auth import RoleBrief
from app.schemas.staff_users import (
    StaffOptionOut,
    StaffUserCreatedOut,
    StaffUserCreateIn,
    StaffUserListQuery,
    StaffUserOut,
    StaffUserUpdateIn,
    TempPasswordOut,
)

_NOW = datetime(2026, 9, 1, 8, 0, tzinfo=UTC)


def _create(**overrides: Any) -> StaffUserCreateIn:
    data: dict[str, Any] = {
        "username": "lin.teacher",
        "display_name": "林老師",
        "role_id": uuid4(),
    }
    data.update(overrides)
    return StaffUserCreateIn.model_validate(data)


def _user_out(**overrides: Any) -> StaffUserOut:
    data: dict[str, Any] = {
        "id": uuid4(),
        "username": "lin.teacher",
        "display_name": "林老師",
        "phone": None,
        "email": None,
        "role": RoleBrief(id=uuid4(), code="tutor", name="導師"),
        "extra_permissions": [],
        "revoked_permissions": [],
        "effective_permissions": ["homework:read", "homework:write"],
        "is_active": True,
        "must_change_password": True,
        "last_login_at": None,
        "created_at": _NOW,
    }
    data.update(overrides)
    return StaffUserOut.model_validate(data)


def test_staff_user_schemas_username() -> None:
    for bad in ("a", "ab", "-abc", ".abc", "has space", "x" * 33, "中文帳號", "a/b"):
        with pytest.raises(ValidationError):
            _create(username=bad)
    assert _create(username="Lin.Teacher").username == "Lin.Teacher"  # service 再轉小寫
    assert _create(username="abc").username == "abc"
    assert _create(username="a" + "b" * 31).username == "a" + "b" * 31
    assert _create(username=" lin_t-1 ").username == "lin_t-1"


def test_staff_user_schemas_email() -> None:
    for bad in ("not-an-email", "a b@c.com", "a@@b.com", "@b.com", "a@", "x" * 250 + "@b.com"):
        with pytest.raises(ValidationError):
            _create(email=bad)
    assert _create(email=None).email is None
    assert _create().email is None
    assert _create(email="lin@example.com").email == "lin@example.com"


def test_staff_user_schemas_create_fields() -> None:
    with pytest.raises(ValidationError):
        _create(display_name="")
    with pytest.raises(ValidationError):
        _create(display_name="名" * 51)
    with pytest.raises(ValidationError):
        _create(phone="0" * 21)
    with pytest.raises(ValidationError):
        _create(role_id="not-a-uuid")
    with pytest.raises(ValidationError):
        _create(password="Passw0rd-Test1")  # 密碼由系統產生臨時密碼，不接受 client 指定
    with pytest.raises(ValidationError):
        StaffUserCreateIn.model_validate({"username": "lin.teacher", "display_name": "林老師"})
    ok = _create()
    assert ok.extra_permissions == []
    assert ok.revoked_permissions == []
    assert ok.phone is None
    assert _create(display_name="名" * 50).display_name == "名" * 50
    assert _create(extra_permissions=["students:read"]).extra_permissions == ["students:read"]


def test_staff_user_schemas_update_rules() -> None:
    with pytest.raises(ValidationError):
        StaffUserUpdateIn.model_validate({})
    with pytest.raises(ValidationError) as excinfo:
        StaffUserUpdateIn.model_validate({"username": "x"})
    assert excinfo.value.errors()[0]["type"] == "extra_forbidden"
    with pytest.raises(ValidationError):
        StaffUserUpdateIn.model_validate({"display_name": None})
    with pytest.raises(ValidationError):
        StaffUserUpdateIn.model_validate({"role_id": None})
    with pytest.raises(ValidationError):
        StaffUserUpdateIn.model_validate({"extra_permissions": None})
    with pytest.raises(ValidationError):
        StaffUserUpdateIn.model_validate({"email": "bad"})

    cleared = StaffUserUpdateIn.model_validate({"phone": None, "email": None})
    assert cleared.model_fields_set == {"phone", "email"}
    only = StaffUserUpdateIn.model_validate({"display_name": "新名字"})
    assert only.model_fields_set == {"display_name"}
    assert StaffUserUpdateIn.model_validate({"revoked_permissions": []}).revoked_permissions == []


def test_staff_user_schemas_list_query() -> None:
    default = StaffUserListQuery()
    assert (default.q, default.role_id, default.is_active) == (None, None, None)
    rid = uuid4()
    q = StaffUserListQuery(q=" 林 ", role_id=rid, is_active=False)
    assert q.q == "林"
    assert q.role_id == rid
    assert q.is_active is False
    with pytest.raises(ValidationError):
        StaffUserListQuery(q="x" * 51)
    with pytest.raises(ValidationError):
        StaffUserListQuery.model_validate({"foo": 1})
    with pytest.raises(ValidationError):
        StaffUserListQuery.model_validate({"is_active": "maybe"})


def test_staff_user_schemas_outputs() -> None:
    user = _user_out()
    body = user.model_dump(mode="json")
    assert set(body) == {
        "id",
        "username",
        "display_name",
        "phone",
        "email",
        "role",
        "extra_permissions",
        "revoked_permissions",
        "effective_permissions",
        "is_active",
        "must_change_password",
        "last_login_at",
        "created_at",
    }
    assert body["role"]["code"] == "tutor"
    # 不得外洩密碼雜湊或 token_version
    assert "password_hash" not in body
    assert "token_version" not in body

    created = StaffUserCreatedOut(user=user, temp_password="Tmp-Passw0rd9")  # noqa: S106
    assert created.temp_password == "Tmp-Passw0rd9"  # noqa: S105
    assert TempPasswordOut(temp_password="x").temp_password == "x"  # noqa: S106
    option = StaffOptionOut(id=uuid4(), display_name="林老師")
    assert set(option.model_dump()) == {"id", "display_name"}
