"""BACKEND-351：GET /api/admin/leaves。
BACKEND-354：GET /api/admin/leaves/{leave_id}/attachments/{attachment_id}（簽發短效 URL）。
BACKEND-352：POST /api/admin/leaves（員工代登記）。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.account import StaffUser
from app.models.attendance import StudentAttendance
from app.models.leaves import StudentLeave
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


# --- BACKEND-352：POST /api/admin/leaves（員工代登記）---------------------------------------------


def _leave_body(student_id: object, **overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "student_id": str(student_id),
        "leave_type": "sick",
        "start_date": "2026-09-01",
        "end_date": "2026-09-02",
    }
    body.update(overrides)
    return body


def test_admin_leaves_create_success(staff_client: StaffClientFactory, db_session: Session) -> None:
    ming = make_student(db_session, name="王小明")
    client, staff = staff_client(permissions=["leaves:write", "leaves:read"], display_name="陳行政")

    resp = client.post(_URL, json=_leave_body(ming.id, reason="發燒"))

    assert resp.status_code == 201
    body = resp.json()
    assert body["student"]["name"] == "王小明"
    assert (body["leave_type"], body["leave_type_label"]) == ("sick", "病假")
    assert (body["start_date"], body["end_date"]) == ("2026-09-01", "2026-09-02")
    assert body["status"] == "active"
    assert body["created_by_type"] == "staff"
    assert body["created_by_name"] == staff.display_name == "陳行政"
    assert body["reason"] == "發燒"
    assert body["attachments"] == []
    # 已 commit：重讀 DB、列表查得到、期間內出勤為 leave
    db_session.expire_all()
    leave = db_session.execute(
        select(StudentLeave).where(StudentLeave.id == UUID(body["id"]))
    ).scalar_one()
    assert (leave.student_id, leave.created_by_type, leave.created_by_id) == (
        ming.id,
        "staff",
        staff.id,
    )
    listed = client.get(_URL, params={"student_id": str(ming.id)}).json()
    assert [i["id"] for i in listed["items"]] == [body["id"]]
    attendance = db_session.execute(
        select(StudentAttendance).where(
            StudentAttendance.student_id == ming.id,
            StudentAttendance.service_date == date(2026, 9, 1),
        )
    ).scalar_one()
    assert (attendance.status, attendance.leave_id) == ("leave", leave.id)


def test_admin_leaves_create_422(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    ming = make_student(db_session)
    client, _ = staff_client(permissions=["leaves:write"])

    reversed_range = client.post(
        _URL, json=_leave_body(ming.id, start_date="2026-09-05", end_date="2026-09-01")
    )
    extra = client.post(_URL, json=_leave_body(ming.id, status="active"))
    bad_type = client.post(_URL, json=_leave_body(ming.id, leave_type="vacation"))
    missing = client.post(_URL, json={"student_id": str(ming.id)})

    for resp in (reversed_range, extra, bad_type, missing):
        assert_error(resp, 422, "validation_error")
    assert (
        db_session.execute(select(StudentLeave).where(StudentLeave.student_id == ming.id)).first()
        is None
    )


def test_admin_leaves_create_401(api_client: TestClient, assert_error: AssertError) -> None:
    assert_error(api_client.post(_URL, json=_leave_body(uuid4())), 401, "unauthenticated")


def test_admin_leaves_create_403(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    ming = make_student(db_session)
    client, _ = staff_client(permissions=["leaves:read"])

    resp = client.post(_URL, json=_leave_body(ming.id))

    assert_error(resp, 403, "permission_denied")
    assert resp.json()["error"]["details"] == {"required": ["leaves:write"]}


def test_admin_leaves_create_409(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    ming = make_student(db_session)
    make_leave(db_session, ming, start_date=date(2026, 9, 2), end_date=date(2026, 9, 3))
    client, _ = staff_client(permissions=["leaves:write"])

    resp = client.post(_URL, json=_leave_body(ming.id))

    assert_error(resp, 409, "leave_overlap")
    assert_error(client.post(_URL, json=_leave_body(uuid4())), 404, "student_not_found")
    db_session.expire_all()
    assert (
        db_session.execute(
            select(func.count()).select_from(StudentLeave).where(StudentLeave.student_id == ming.id)
        ).scalar_one()
        == 1
    )


def test_admin_leaves_create_many_prior_leaves(
    staff_client: StaffClientFactory, db_session: Session
) -> None:
    """該生已有 200 筆以上 start_date 較晚的（已取消）請假時，新請假仍 201 且回傳的是新建那筆。"""
    ming = make_student(db_session, name="王小明")
    for offset in range(205):
        make_leave(
            db_session,
            ming,
            start_date=date(2026, 10, 1) + timedelta(days=offset),
            status="cancelled",
        )
    client, _ = staff_client(permissions=["leaves:write"])

    resp = client.post(
        _URL, json=_leave_body(ming.id, start_date="2026-09-01", end_date="2026-09-01")
    )

    assert resp.status_code == 201
    body = resp.json()
    assert (body["start_date"], body["status"]) == ("2026-09-01", "active")
    db_session.expire_all()
    leave = db_session.execute(
        select(StudentLeave).where(StudentLeave.id == UUID(body["id"]))
    ).scalar_one()
    assert (leave.student_id, leave.start_date, leave.status) == (
        ming.id,
        date(2026, 9, 1),
        "active",
    )
