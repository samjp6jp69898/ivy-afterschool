"""BACKEND-105：GET /api/admin/audit-logs。"""

from __future__ import annotations

from collections.abc import Callable
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.account import StaffUser
from app.services.audit_service import Actor, record
from tests.support.route_audit import admin_routes_without_permission

_URL = "/api/admin/audit-logs"
StaffClientFactory = Callable[..., tuple[TestClient, StaffUser]]
AssertError = Callable[..., None]
_TOKEN = "tok-abcdefgh9999"  # noqa: S105  測試假值


def test_admin_audit_logs_success(staff_client: StaffClientFactory, db_session: Session) -> None:
    client, staff = staff_client(permissions=["audit:read"])
    record(
        db_session,
        actor=Actor("staff", staff.id),
        action="settings.update",
        entity_type="setting",
        entity_id="org.profile",
        before={"name": "A"},
        after={"name": "B"},
    )
    record(
        db_session,
        actor=Actor.system(),
        action="role.create",
        entity_type="role",
        entity_id=str(uuid4()),
    )
    db_session.commit()

    resp = client.get(_URL, params={"action": "settings.update"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 1
    first = body["items"][0]
    assert first["action"] == "settings.update"
    assert first["actor_name"] == staff.display_name
    assert first["actor_type"] == "staff"
    assert first["before"] == {"name": "A"}
    assert first["after"] == {"name": "B"}
    assert all(i["action"] == "settings.update" for i in body["items"])


def test_admin_audit_logs_pagination_params_accepted(
    staff_client: StaffClientFactory, db_session: Session
) -> None:
    client, _ = staff_client(permissions=["audit:read"])
    for n in range(3):
        record(
            db_session,
            actor=Actor.system(),
            action="role.create",
            entity_type="paging_probe",
            entity_id=str(n),
        )
    db_session.commit()

    resp = client.get(_URL, params={"entity_type": "paging_probe", "page": 2, "page_size": 2})

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    assert len(body["items"]) == 1
    assert client.get(_URL, params={"page_size": 201}).status_code == 422


def test_admin_audit_logs_422(staff_client: StaffClientFactory, assert_error: AssertError) -> None:
    client, _ = staff_client(permissions=["audit:read"])

    robot = client.get(_URL, params={"actor_type": "robot"})
    reversed_dates = client.get(_URL, params={"date_from": "2026-09-02", "date_to": "2026-09-01"})
    unknown = client.get(_URL, params={"foo": "bar"})
    bad_uuid = client.get(_URL, params={"actor_id": "not-a-uuid"})

    for resp in (robot, reversed_dates, unknown, bad_uuid):
        assert_error(resp, 422, "validation_error")


def test_admin_audit_logs_401(api_client: TestClient, assert_error: AssertError) -> None:
    assert_error(api_client.get(_URL), 401, "unauthenticated")


def test_admin_audit_logs_403(staff_client: StaffClientFactory, assert_error: AssertError) -> None:
    client, _ = staff_client(permissions=["settings:read"])

    resp = client.get(_URL)

    assert_error(resp, 403, "permission_denied")
    assert resp.json()["error"]["details"] == {"required": ["audit:read"]}


def test_admin_audit_logs_masked(staff_client: StaffClientFactory, db_session: Session) -> None:
    client, staff = staff_client(permissions=["audit:read"])
    record(
        db_session,
        actor=Actor("staff", staff.id),
        action="settings.update",
        entity_type="setting",
        entity_id="line.messaging",
        after={"channel_access_token": _TOKEN, "channel_secret": "sec-xyz-123456"},
    )
    db_session.commit()

    resp = client.get(_URL, params={"entity_id": "line.messaging"})

    assert resp.status_code == 200
    after = resp.json()["items"][0]["after"]
    # 寫入端（BACKEND-102）遮罩：敏感 key 的值一律為 '***'
    assert after == {"channel_access_token": "***", "channel_secret": "***"}
    assert _TOKEN not in resp.text
    assert "sec-xyz-123456" not in resp.text


def test_admin_audit_logs_guard_registered(app: FastAPI) -> None:
    assert admin_routes_without_permission(app) == []
    assert "/api/admin/audit-logs" in app.openapi()["paths"]
