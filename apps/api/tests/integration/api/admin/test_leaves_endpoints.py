"""BACKEND-351：GET /api/admin/leaves。
BACKEND-354：GET /api/admin/leaves/{leave_id}/attachments/{attachment_id}（簽發短效 URL）。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.account import StaffUser
from tests.support.factories import (
    make_class,
    make_leave,
    make_leave_attachment,
    make_parent,
    make_student,
)
from tests.support.route_audit import admin_routes_without_permission

_URL = "/api/admin/leaves"
StaffClientFactory = Callable[..., tuple[TestClient, StaffUser]]
AssertError = Callable[..., None]


def test_admin_leaves_guard_registered(app: FastAPI) -> None:
    assert admin_routes_without_permission(app) == []
    paths = app.openapi()["paths"]
    assert "get" in paths[_URL]
    assert "get" in paths[_URL + "/{leave_id}/attachments/{attachment_id}"]


# --- BACKEND-351：GET /api/admin/leaves --------------------------------------------------------


def test_admin_leaves_list_success(staff_client: StaffClientFactory, db_session: Session) -> None:
    class_a = make_class(db_session, name="A班")
    ming = make_student(db_session, name="王小明", class_=class_a)
    parent = make_parent(db_session, display_name="王媽媽")
    leave = make_leave(
        db_session,
        ming,
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 2),
        created_by_type="parent",
        created_by_id=parent.id,
        reason="發燒",
    )
    make_leave_attachment(db_session, leave)
    # 別人的請假：以 class_id 篩選排除
    make_leave(db_session, make_student(db_session, name="陳小華"), start_date=date(2026, 9, 3))
    client, _ = staff_client(permissions=["leaves:read"])

    resp = client.get(_URL, params={"class_id": str(class_a.id)})

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["id"] == str(leave.id)
    assert item["student"]["name"] == "王小明"
    assert item["student"]["class_name"] == "A班"
    assert item["leave_type"] == "sick"
    assert item["leave_type_label"] == "病假"
    assert item["status"] == "active"
    assert item["created_by_type"] == "parent"
    assert item["created_by_name"] == "王媽媽"
    assert len(item["attachments"]) == 1
    assert set(item["attachments"][0]) == {"id", "mime_type", "size_bytes", "created_at"}
    assert set(item) == {
        "id",
        "student",
        "leave_type",
        "leave_type_label",
        "start_date",
        "end_date",
        "reason",
        "status",
        "created_by_type",
        "created_by_name",
        "created_at",
        "cancelled_at",
        "cancelled_by_type",
        "cancelled_by_name",
        "attachments",
    }
    # 分頁 / 篩選參數
    page = client.get(_URL, params={"class_id": str(class_a.id), "page": 2, "page_size": 1})
    assert page.json() == {"items": [], "total": 1}


def test_admin_leaves_list_422(staff_client: StaffClientFactory, assert_error: AssertError) -> None:
    client, _ = staff_client(permissions=["leaves:read"])

    assert_error(client.get(_URL, params={"status": "pending"}), 422, "validation_error")
    assert_error(
        client.get(_URL, params={"date_from": "2026-09-05", "date_to": "2026-09-01"}),
        422,
        "validation_error",
    )
    assert_error(client.get(_URL, params={"student_id": "abc"}), 422, "validation_error")
    assert_error(client.get(_URL, params={"foo": "bar"}), 422, "validation_error")


def test_admin_leaves_list_401(api_client: TestClient, assert_error: AssertError) -> None:
    assert_error(api_client.get(_URL), 401, "unauthenticated")


def test_admin_leaves_list_403(staff_client: StaffClientFactory, assert_error: AssertError) -> None:
    client, _ = staff_client(permissions=["attendance:read"])

    resp = client.get(_URL)

    assert_error(resp, 403, "permission_denied")
    assert resp.json()["error"]["details"] == {"required": ["leaves:read"]}


def test_admin_leaves_list_empty_filter(
    staff_client: StaffClientFactory, db_session: Session
) -> None:
    make_leave(db_session, make_student(db_session), start_date=date(2026, 9, 1))
    client, _ = staff_client(permissions=["leaves:read"])

    resp = client.get(_URL, params={"student_id": str(uuid4())})

    assert resp.status_code == 200
    assert resp.json() == {"items": [], "total": 0}


# --- BACKEND-354：GET /api/admin/leaves/{leave_id}/attachments/{attachment_id} -----------------


def _attachment_url(leave_id: object, attachment_id: object) -> str:
    return f"{_URL}/{leave_id}/attachments/{attachment_id}"


def test_admin_leave_attachment_url_success(
    staff_client: StaffClientFactory, db_session: Session
) -> None:
    leave = make_leave(db_session, make_student(db_session), start_date=date(2026, 9, 1))
    attachment = make_leave_attachment(db_session, leave)
    client, _ = staff_client(permissions=["leaves:read"])

    resp = client.get(_attachment_url(leave.id, attachment.id))

    assert resp.status_code == 200
    body = resp.json()
    assert body["url"].startswith("https://storage.test/leave-attachments/")
    assert body["url"] == (
        f"https://storage.test/leave-attachments/{attachment.storage_path}?exp=300"
    )
    assert body["expires_in"] == 300
    assert set(body) == {"url", "expires_in"}
    assert resp.headers["cache-control"] == "no-store"


def test_admin_leave_attachment_url_422(
    staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    client, _ = staff_client(permissions=["leaves:read"])

    assert_error(client.get(_attachment_url(uuid4(), "abc")), 422, "validation_error")
    assert_error(client.get(_attachment_url("abc", uuid4())), 422, "validation_error")


def test_admin_leave_attachment_url_401(api_client: TestClient, assert_error: AssertError) -> None:
    assert_error(api_client.get(_attachment_url(uuid4(), uuid4())), 401, "unauthenticated")


def test_admin_leave_attachment_url_403(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    leave = make_leave(db_session, make_student(db_session), start_date=date(2026, 9, 1))
    attachment = make_leave_attachment(db_session, leave)
    client, _ = staff_client(permissions=["attendance:read"])

    assert_error(client.get(_attachment_url(leave.id, attachment.id)), 403, "permission_denied")


def test_admin_leave_attachment_url_404(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    leave = make_leave(db_session, make_student(db_session), start_date=date(2026, 9, 1))
    other = make_leave(db_session, make_student(db_session), start_date=date(2026, 9, 2))
    attachment = make_leave_attachment(db_session, other)
    client, _ = staff_client(permissions=["leaves:read"])

    # 附件屬於另一筆請假 → 與不存在相同的 404
    mismatched = client.get(_attachment_url(leave.id, attachment.id))
    missing = client.get(_attachment_url(leave.id, uuid4()))

    assert_error(mismatched, 404, "attachment_not_found")
    assert_error(missing, 404, "attachment_not_found")
    assert mismatched.json() == missing.json()
