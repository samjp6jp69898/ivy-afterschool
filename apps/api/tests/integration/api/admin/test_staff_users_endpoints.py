"""BACKEND-093：GET /api/admin/staff-users（staff:read；分頁、搜尋、篩選）。"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.account import StaffUser
from tests.support.factories import make_staff
from tests.support.route_audit import admin_routes_without_permission

_URL = "/api/admin/staff-users"
StaffClientFactory = Callable[..., tuple[TestClient, StaffUser]]
AssertError = Callable[..., None]


def test_admin_staff_list_success(staff_client: StaffClientFactory, db_session: Session) -> None:
    lin = make_staff(db_session, username="lin.teacher", role_code="tutor", display_name="林老師")
    make_staff(db_session, username="chen.clerk", role_code="clerk", display_name="陳行政")
    make_staff(
        db_session, username="lin.b", role_code="tutor", display_name="離職", is_active=False
    )
    client, _ = staff_client(permissions=["staff:read"])

    resp = client.get(_URL, params={"q": "lin"})

    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"items", "total"}
    assert body["total"] == 2
    assert body["items"][0]["username"].startswith("lin")
    # 啟用在前
    assert [(u["username"], u["is_active"]) for u in body["items"]] == [
        ("lin.teacher", True),
        ("lin.b", False),
    ]
    first = body["items"][0]
    assert first["id"] == str(lin.id)
    assert first["role"]["code"] == "tutor"
    assert "homework:write" in first["effective_permissions"]
    assert set(first) == {
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
    # 篩選 + 分頁
    inactive = client.get(_URL, params={"q": "lin", "is_active": "false"}).json()
    assert [u["username"] for u in inactive["items"]] == ["lin.b"]
    page = client.get(_URL, params={"q": "lin", "page": 2, "page_size": 1}).json()
    assert (page["total"], [u["username"] for u in page["items"]]) == (2, ["lin.b"])
    by_role = client.get(_URL, params={"role_id": str(lin.role_id), "q": "lin"}).json()
    assert {u["username"] for u in by_role["items"]} == {"lin.teacher", "lin.b"}


def test_admin_staff_list_422(staff_client: StaffClientFactory, assert_error: AssertError) -> None:
    client, _ = staff_client(permissions=["staff:read"])

    assert_error(client.get(_URL, params={"page_size": 500}), 422, "validation_error")
    assert_error(client.get(_URL, params={"role_id": "abc"}), 422, "validation_error")
    assert_error(client.get(_URL, params={"is_active": "maybe"}), 422, "validation_error")
    assert_error(client.get(_URL, params={"foo": "bar"}), 422, "validation_error")


def test_admin_staff_list_401(api_client: TestClient, assert_error: AssertError) -> None:
    assert_error(api_client.get(_URL), 401, "unauthenticated")


def test_admin_staff_list_403(staff_client: StaffClientFactory, assert_error: AssertError) -> None:
    client, _ = staff_client(permissions=["roles:read"])

    resp = client.get(_URL)

    assert_error(resp, 403, "permission_denied")
    assert resp.json()["error"]["details"] == {"required": ["staff:read"]}


def test_admin_staff_list_no_password_hash(
    staff_client: StaffClientFactory, db_session: Session
) -> None:
    make_staff(db_session, username="hash.check", role_code="tutor")
    client, _ = staff_client(permissions=["staff:read"])

    resp = client.get(_URL, params={"q": "hash.check"})

    assert resp.status_code == 200
    assert resp.json()["total"] == 1
    assert "$argon2id$" not in resp.text
    assert "password" not in resp.text


def test_admin_staff_list_guard_registered(app: FastAPI) -> None:
    assert admin_routes_without_permission(app) == []
    assert "get" in app.openapi()["paths"][_URL]
