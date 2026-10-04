"""BACKEND-081 / 084 / 085：roles 列表與刪除、permissions 目錄。"""

from __future__ import annotations

from collections.abc import Callable
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.permissions import PERMISSION_GROUPS, PERMISSION_LABELS, Permission
from app.models.account import Role, StaffUser
from app.models.audit import AuditLog
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


def _delete_url(role_id: object) -> str:
    return f"{_URL}/{role_id}"


def test_admin_roles_delete_success(staff_client: StaffClientFactory, db_session: Session) -> None:
    role = make_role(db_session, code="temp_role", name="臨時角色")
    client, staff = staff_client(permissions=["roles:write", "roles:read"])

    resp = client.delete(_delete_url(role.id))

    assert resp.status_code == 204
    assert resp.content == b""
    codes = [r["code"] for r in client.get(_URL).json()]
    assert "temp_role" not in codes
    assert db_session.execute(select(Role).where(Role.code == "temp_role")).first() is None
    log = db_session.execute(
        select(AuditLog).where(AuditLog.action == "role.delete", AuditLog.entity_id == str(role.id))
    ).scalar_one()
    assert log.actor_id == staff.id
    assert log.before is not None
    assert log.before["code"] == "temp_role"


def test_admin_roles_delete_422(
    staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    client, _ = staff_client(permissions=["roles:write"])

    assert_error(client.delete(_delete_url("abc")), 422, "validation_error")


def test_admin_roles_delete_401(api_client: TestClient, assert_error: AssertError) -> None:
    assert_error(api_client.delete(_delete_url(uuid4())), 401, "unauthenticated")


def test_admin_roles_delete_403(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    role = make_role(db_session, code="keep_me")
    client, _ = staff_client(permissions=["roles:read"])

    resp = client.delete(_delete_url(role.id))

    assert_error(resp, 403, "permission_denied")
    assert resp.json()["error"]["details"] == {"required": ["roles:write"]}
    assert db_session.execute(select(Role).where(Role.code == "keep_me")).first() is not None


def test_admin_roles_delete_404(
    staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    client, _ = staff_client(permissions=["roles:write"])

    assert_error(client.delete(_delete_url(uuid4())), 404, "role_not_found")


def test_admin_roles_delete_409(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    tutor = db_session.execute(select(Role).where(Role.code == "tutor")).scalar_one()
    in_use = make_role(db_session, code="in_use_role")
    make_staff(db_session, is_active=False).role = in_use
    db_session.flush()
    client, _ = staff_client(permissions=["roles:write"])

    system = client.delete(_delete_url(tutor.id))
    used = client.delete(_delete_url(in_use.id))

    assert_error(system, 409, "system_role_protected")
    assert_error(used, 409, "role_in_use")
    assert used.json()["error"]["details"] == {"staff_count": 1}
    assert db_session.execute(select(Role).where(Role.code == "in_use_role")).first() is not None


def test_admin_roles_delete_guard_registered(app: FastAPI) -> None:
    assert admin_routes_without_permission(app) == []
    assert "delete" in app.openapi()["paths"]["/api/admin/roles/{role_id}"]


def test_admin_permissions_catalog(staff_client: StaffClientFactory) -> None:
    client, _ = staff_client(permissions=["roles:read"])

    resp = client.get("/api/admin/permissions")

    assert resp.status_code == 200
    groups = resp.json()["groups"]
    assert [g["key"] for g in groups] == [
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
    assert [g["key"] for g in groups] == [g.key for g in PERMISSION_GROUPS]
    codes = [p["code"] for g in groups for p in g["permissions"]]
    assert len(codes) == 28
    assert codes == [str(p) for g in PERMISSION_GROUPS for p in g.permissions]
    assert set(codes) == {p.value for p in Permission}


def test_admin_permissions_label(staff_client: StaffClientFactory) -> None:
    client, _ = staff_client(permissions=["roles:read"])

    groups = client.get("/api/admin/permissions").json()["groups"]

    students = next(g for g in groups if g["key"] == "students")
    expected = {
        "code": "students:sensitive",
        "label": PERMISSION_LABELS[Permission.STUDENTS_SENSITIVE],
    }
    assert expected in students["permissions"]
    assert expected["label"] == "查看與寫入身分證、健康備註"
    assert students["label"] == "班級與學生"
    assert set(groups[0]) == {"key", "label", "permissions"}


def test_admin_permissions_401(api_client: TestClient, assert_error: AssertError) -> None:
    assert_error(api_client.get("/api/admin/permissions"), 401, "unauthenticated")


def test_admin_permissions_403(staff_client: StaffClientFactory, assert_error: AssertError) -> None:
    client, _ = staff_client(permissions=["students:read"])

    assert_error(client.get("/api/admin/permissions"), 403, "permission_denied")


def test_admin_permissions_staff_read_allowed(staff_client: StaffClientFactory) -> None:
    client, _ = staff_client(permissions=["staff:read"])

    assert client.get("/api/admin/permissions").status_code == 200


def test_admin_permissions_guard_registered(app: FastAPI) -> None:
    assert admin_routes_without_permission(app) == []
    assert "/api/admin/permissions" in app.openapi()["paths"]
