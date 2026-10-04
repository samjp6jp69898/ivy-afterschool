"""BACKEND-081：GET /api/admin/roles。"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.account import StaffUser
from tests.support.factories import make_role, make_staff
from tests.support.route_audit import admin_routes_without_permission

_URL = "/api/admin/roles"
StaffClientFactory = Callable[..., tuple[TestClient, StaffUser]]
AssertError = Callable[..., None]


def test_admin_roles_list_success(staff_client: StaffClientFactory) -> None:
    client, _ = staff_client(permissions=["roles:read"])

    resp = client.get(_URL)

    assert resp.status_code == 200
    body = resp.json()
    assert {r["code"] for r in body[:4]} == {"admin", "director", "clerk", "tutor"}
    assert all(r["is_system"] for r in body[:4])
    admin = next(r for r in body if r["code"] == "admin")
    assert admin["permissions"] == ["*"]
    assert len(admin["effective_permissions"]) == 28
    assert set(admin) == {
        "id",
        "code",
        "name",
        "description",
        "is_system",
        "permissions",
        "effective_permissions",
        "staff_count",
        "created_at",
        "updated_at",
    }


def test_admin_roles_list_staff_read_allowed(staff_client: StaffClientFactory) -> None:
    client, _ = staff_client(permissions=["staff:read"])

    resp = client.get(_URL)

    assert resp.status_code == 200
    assert len(resp.json()) >= 4


def test_admin_roles_list_401(api_client: TestClient, assert_error: AssertError) -> None:
    assert_error(api_client.get(_URL), 401, "unauthenticated")


def test_admin_roles_list_403(staff_client: StaffClientFactory, assert_error: AssertError) -> None:
    client, _ = staff_client(permissions=["students:read"])

    resp = client.get(_URL)

    assert_error(resp, 403, "permission_denied")
    assert set(resp.json()["error"]["details"]["required"]) == {"roles:read", "staff:read"}


def test_admin_roles_list_counts(staff_client: StaffClientFactory, db_session: Session) -> None:
    role = make_role(db_session, code="front_desk", permissions=["students:read"])
    make_staff(db_session).role = role
    make_staff(db_session).role = role
    make_staff(db_session, is_active=False).role = role
    db_session.flush()
    client, _ = staff_client(permissions=["roles:read"])

    body = client.get(_URL).json()

    assert next(r for r in body if r["code"] == "front_desk")["staff_count"] == 2
    assert [r["code"] for r in body[4:]] == sorted(r["code"] for r in body[4:])


def test_admin_roles_list_guard_registered(app: FastAPI) -> None:
    assert admin_routes_without_permission(app) == []
    assert "/api/admin/roles" in app.openapi()["paths"]
