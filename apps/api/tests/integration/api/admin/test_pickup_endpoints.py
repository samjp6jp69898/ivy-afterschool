"""BACKEND-429：GET /api/admin/pickup/queue。
BACKEND-435：GET /api/admin/pickup/roster（POS 學生卡）。
BACKEND-436：GET /api/admin/pickup/authorizations（代理接送核驗清單）。
BACKEND-430 / 432 / 434：POST /api/admin/pickup/requests、/{id}/acknowledge、/{id}/cancel。
BACKEND-431：POST /api/admin/pickup/requests/{id}/reply。
BACKEND-433：POST /api/admin/pickup/requests/{id}/complete。
BACKEND-437：POST /api/admin/pickup/authorizations/{id}/verify（錯碼累計先 commit）。
BACKEND-438：POST /api/admin/pickup/authorizations/{id}/confirm-visual-match。

fake_clock 預設 2026-09-01 01:00 UTC（台北 09:00）；接送請求 / 授權的 service_date 用台北「今天」。
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime, time
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.storage import build_object_path
from app.models.account import StaffUser
from app.models.attendance import StudentAttendance
from app.models.audit import AuditLog
from app.models.homework import HomeworkDailyProgress
from app.models.notifications import Notification
from app.models.parents import ParentAccount
from app.models.pickup import PickupAuthorization, PickupRequest
from app.models.students import Student
from app.notifications import outbox_jobs
from app.services.settings_service import clear_settings_cache
from tests.support.factories import (
    make_attendance,
    make_class,
    make_guardian,
    make_homework_progress,
    make_leave,
    make_parent,
    make_pickup_authorization,
    make_pickup_person,
    make_pickup_request,
    make_staff,
    make_student,
)
from tests.support.fake_clock import FakeClock
from tests.support.route_audit import admin_routes_without_permission

_URL = "/api/admin/pickup"
_TODAY = date(2026, 9, 1)
StaffClientFactory = Callable[..., tuple[TestClient, StaffUser]]
ParentClientFactory = Callable[..., tuple[TestClient, ParentAccount]]
AssertError = Callable[..., None]


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    clear_settings_cache()
    yield
    clear_settings_cache()


def test_admin_pickup_guard_registered(app: FastAPI) -> None:
    assert admin_routes_without_permission(app) == []
    paths = app.openapi()["paths"]
    assert "get" in paths[f"{_URL}/queue"]
    assert "get" in paths[f"{_URL}/roster"]
    assert "get" in paths[f"{_URL}/authorizations"]


# --- BACKEND-429：GET /queue ------------------------------------------------------------------


def test_admin_pickup_queue_success(staff_client: StaffClientFactory, db_session: Session) -> None:
    ming = make_student(db_session, name="王小明")
    hua = make_student(db_session, name="陳小華")
    arrived = make_pickup_request(
        db_session, ming, service_date=_TODAY, status="arrived", reply_source="staff"
    )
    pending = make_pickup_request(db_session, hua, service_date=_TODAY)
    completed = make_pickup_request(
        db_session, make_student(db_session), service_date=_TODAY, status="completed"
    )
    client, _ = staff_client(permissions=["pickup:read"])

    resp = client.get(f"{_URL}/queue")

    assert resp.status_code == 200
    body = resp.json()
    assert body["date"] == "2026-09-01"
    assert [o["id"] for o in body["open"]] == [str(arrived.id), str(pending.id)]
    assert body["open"][0]["status"] == "arrived"
    assert body["open"][0]["student"]["name"] == "王小明"
    assert body["open"][1]["needs_reply"] is True
    assert [c["id"] for c in body["closed"]] == [str(completed.id)]
    assert body["counts"] == {"pending": 1, "acknowledged": 0, "arrived": 1, "needs_reply": 1}
    assert set(body) == {"date", "open", "closed", "counts"}


def test_admin_pickup_queue_422(
    staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    client, _ = staff_client(permissions=["pickup:read"])

    assert_error(client.get(f"{_URL}/queue", params={"date": "bad"}), 422, "validation_error")
    assert_error(client.get(f"{_URL}/queue", params={"foo": "bar"}), 422, "validation_error")


def test_admin_pickup_queue_401(api_client: TestClient, assert_error: AssertError) -> None:
    assert_error(api_client.get(f"{_URL}/queue"), 401, "unauthenticated")


def test_admin_pickup_queue_403(
    staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    client, _ = staff_client(permissions=["homework:read"])

    resp = client.get(f"{_URL}/queue")

    assert_error(resp, 403, "permission_denied")
    assert resp.json()["error"]["details"] == {"required": ["pickup:read"]}


def test_admin_pickup_queue_empty_day(
    staff_client: StaffClientFactory, db_session: Session
) -> None:
    make_pickup_request(db_session, make_student(db_session), service_date=_TODAY)
    client, _ = staff_client(permissions=["pickup:read"])

    resp = client.get(f"{_URL}/queue", params={"date": "2026-08-03"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["date"] == "2026-08-03"
    assert body["open"] == []
    assert body["closed"] == []
    assert body["counts"] == {"pending": 0, "acknowledged": 0, "arrived": 0, "needs_reply": 0}


# --- BACKEND-435：GET /roster -----------------------------------------------------------------


def test_admin_pickup_roster_success(staff_client: StaffClientFactory, db_session: Session) -> None:
    class_a = make_class(db_session, name="甲班")
    ming = make_student(db_session, name="王小明", student_no="R-001", class_=class_a)
    make_student(db_session, name="陳小華", student_no="R-002", class_=class_a)
    make_pickup_request(db_session, ming, service_date=_TODAY)
    make_pickup_authorization(db_session, ming, service_date=_TODAY)
    client, _ = staff_client(permissions=["pickup:read"])

    resp = client.get(f"{_URL}/roster", params={"class_id": str(class_a.id)})

    assert resp.status_code == 200
    body = resp.json()
    assert body["date"] == "2026-09-01"
    assert len(body["classes"]) == 1
    group = body["classes"][0]
    assert group["class_name"] == "甲班"
    assert [s["name"] for s in group["students"]] == ["王小明", "陳小華"]
    card = group["students"][0]
    assert card["name"] == "王小明"
    assert card["open_request"]["status"] == "pending"
    assert card["active_authorization_count"] == 1
    assert group["students"][1]["open_request"] is None
    assert set(card) == {
        "student_id",
        "student_no",
        "name",
        "grade_level",
        "attendance_status",
        "check_in_at",
        "check_out_at",
        "leave_type",
        "homework_status",
        "ready_eta",
        "open_request",
        "active_authorization_count",
    }


def test_admin_pickup_roster_422(
    staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    client, _ = staff_client(permissions=["pickup:read"])

    assert_error(client.get(f"{_URL}/roster", params={"class_id": "abc"}), 422, "validation_error")
    assert_error(client.get(f"{_URL}/roster", params={"date": "bad"}), 422, "validation_error")


def test_admin_pickup_roster_401(api_client: TestClient, assert_error: AssertError) -> None:
    assert_error(api_client.get(f"{_URL}/roster"), 401, "unauthenticated")


def test_admin_pickup_roster_403(
    staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    client, _ = staff_client(permissions=["attendance:read"])

    assert_error(client.get(f"{_URL}/roster"), 403, "permission_denied")


def test_admin_pickup_roster_unknown_class(
    staff_client: StaffClientFactory, db_session: Session
) -> None:
    make_student(db_session, class_=make_class(db_session))
    client, _ = staff_client(permissions=["pickup:read"])

    resp = client.get(f"{_URL}/roster", params={"class_id": str(uuid4())})

    assert resp.status_code == 200
    assert resp.json()["classes"] == []


# --- BACKEND-436：GET /authorizations ---------------------------------------------------------


def test_admin_pickup_authorizations_success(
    staff_client: StaffClientFactory, db_session: Session
) -> None:
    ming = make_student(db_session, name="王小明")
    hua = make_student(db_session, name="陳小華")
    first = make_pickup_authorization(db_session, ming, service_date=_TODAY, code="135790")
    second = make_pickup_authorization(
        db_session, hua, service_date=_TODAY, code="246802", proxy_name="陳阿姨"
    )
    # 別天的不在預設日期內
    make_pickup_authorization(db_session, ming, service_date=date(2026, 8, 31))
    client, _ = staff_client(permissions=["pickup:read"])

    resp = client.get(f"{_URL}/authorizations")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    assert {a["id"] for a in body} == {str(first.id), str(second.id)}
    assert all(len(a["code_last4"]) == 4 and a["code_last4"].isdigit() for a in body)
    assert "code_hash" not in resp.text
    assert "135790" not in resp.text
    by_id = {a["id"]: a for a in body}
    assert by_id[str(first.id)]["student"]["name"] == "王小明"
    assert by_id[str(first.id)]["status"] == "active"
    assert by_id[str(first.id)]["effective_status"] == "active"
    assert by_id[str(first.id)]["locked"] is False
    assert by_id[str(second.id)]["proxy_name"] == "陳阿姨"
    assert set(body[0]) >= {
        "id",
        "student",
        "student_id",
        "service_date",
        "proxy_name",
        "proxy_phone",
        "code_last4",
        "status",
        "effective_status",
        "photo_url",
        "code_attempts",
        "locked",
        "verified_by_name",
    }


def test_admin_pickup_authorizations_422(
    staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    client, _ = staff_client(permissions=["pickup:read"])

    assert_error(
        client.get(f"{_URL}/authorizations", params={"status": "expired"}),
        422,
        "validation_error",
    )
    assert_error(
        client.get(f"{_URL}/authorizations", params={"date": "bad"}), 422, "validation_error"
    )


def test_admin_pickup_authorizations_401(api_client: TestClient, assert_error: AssertError) -> None:
    assert_error(api_client.get(f"{_URL}/authorizations"), 401, "unauthenticated")


def test_admin_pickup_authorizations_403(
    staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    client, _ = staff_client(permissions=["homework:read"])

    assert_error(client.get(f"{_URL}/authorizations"), 403, "permission_denied")


def test_admin_pickup_authorizations_filter(
    staff_client: StaffClientFactory, db_session: Session
) -> None:
    ming = make_student(db_session, name="王小明")
    make_pickup_authorization(db_session, ming, service_date=_TODAY)
    done = make_pickup_authorization(
        db_session, make_student(db_session), service_date=_TODAY, status="completed"
    )
    client, _ = staff_client(permissions=["pickup:read"])

    resp = client.get(f"{_URL}/authorizations", params={"status": "completed"})

    assert resp.status_code == 200
    assert [a["id"] for a in resp.json()] == [str(done.id)]
    assert resp.json()[0]["status"] == "completed"
    assert resp.json()[0]["verified_at"] is not None
    other_day = client.get(f"{_URL}/authorizations", params={"date": "2026-08-03"})
    assert other_day.json() == []


# --- BACKEND-430 / 432 / 434：POST /requests、/requests/{id}/acknowledge、/{id}/cancel --------

_REQUESTS = f"{_URL}/requests"


@pytest.fixture(autouse=True)
def _kick_off() -> Iterator[None]:
    """取消 / 回覆會 enqueue 家長通知：commit 後的 outbox kick 不實際派送。"""
    outbox_jobs.set_kick_mode("off")
    yield
    outbox_jobs.set_kick_mode("thread")


def _request_url(request_id: object, suffix: str = "") -> str:
    return f"{_REQUESTS}/{request_id}{suffix}"


def _family(db: Session) -> tuple[Student, ParentAccount]:
    ming = make_student(db, name="王小明")
    parent = make_parent(db, display_name="王媽媽")
    make_guardian(db, ming, parent=parent)
    return ming, parent


def test_admin_pickup_create_success(staff_client: StaffClientFactory, db_session: Session) -> None:
    ming, _ = _family(db_session)
    client, staff = staff_client(
        permissions=["pickup:operate", "pickup:read"], display_name="林老師"
    )

    resp = client.post(_REQUESTS, json={"student_id": str(ming.id), "expected_arrival_at": "17:30"})

    assert resp.status_code == 201
    body = resp.json()
    assert body["source"] == "staff"
    assert body["status"] == "pending"
    assert body["requested_by_type"] == "staff"
    assert body["requested_by_name"] == "林老師"
    assert body["student"]["name"] == "王小明"
    assert body["expected_arrival_at"] == "17:30"
    assert body["service_date"] == "2026-09-01"
    db_session.expire_all()
    row = db_session.execute(
        select(PickupRequest).where(PickupRequest.id == UUID(body["id"]))
    ).scalar_one()
    assert (row.source, row.requested_by_id, row.status) == ("staff", staff.id, "pending")
    assert body["id"] in {o["id"] for o in client.get(f"{_URL}/queue").json()["open"]}


def test_admin_pickup_create_422(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    ming, _ = _family(db_session)
    client, _ = staff_client(permissions=["pickup:operate"])

    bad_time = client.post(
        _REQUESTS, json={"student_id": str(ming.id), "expected_arrival_at": "5pm"}
    )
    extra = client.post(_REQUESTS, json={"student_id": str(ming.id), "source": "parent"})
    missing = client.post(_REQUESTS, json={})

    for resp in (bad_time, extra, missing):
        assert_error(resp, 422, "validation_error")
    assert db_session.execute(select(PickupRequest)).first() is None


def test_admin_pickup_create_401(api_client: TestClient, assert_error: AssertError) -> None:
    assert_error(
        api_client.post(_REQUESTS, json={"student_id": str(uuid4())}), 401, "unauthenticated"
    )


def test_admin_pickup_create_403(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    ming, _ = _family(db_session)
    client, _ = staff_client(permissions=["pickup:read"])

    resp = client.post(_REQUESTS, json={"student_id": str(ming.id)})

    assert_error(resp, 403, "permission_denied")
    assert resp.json()["error"]["details"] == {"required": ["pickup:operate"]}


def test_admin_pickup_create_409(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    ming, _ = _family(db_session)
    make_pickup_request(db_session, ming, service_date=_TODAY)
    on_leave = make_student(db_session, name="請假中")
    leave = make_leave(db_session, on_leave, start_date=_TODAY)
    make_attendance(db_session, on_leave, service_date=_TODAY, status="leave", leave=leave)
    client, _ = staff_client(permissions=["pickup:operate"])

    duplicate = client.post(_REQUESTS, json={"student_id": str(ming.id)})
    unavailable = client.post(_REQUESTS, json={"student_id": str(on_leave.id)})

    assert_error(duplicate, 409, "pickup_request_exists")
    assert_error(unavailable, 409, "student_not_available")
    assert unavailable.json()["error"]["details"] == {"attendance_status": "leave"}
    assert_error(
        client.post(_REQUESTS, json={"student_id": str(uuid4())}), 404, "student_not_found"
    )


def test_admin_pickup_ack_success(staff_client: StaffClientFactory, db_session: Session) -> None:
    ming, parent = _family(db_session)
    request = make_pickup_request(db_session, ming, service_date=_TODAY, requested_by=parent.id)
    client, _ = staff_client(permissions=["pickup:operate"])

    resp = client.post(_request_url(request.id, "/acknowledge"))

    assert resp.status_code == 200
    assert resp.json()["status"] == "acknowledged"
    assert resp.json()["id"] == str(request.id)
    db_session.expire_all()
    assert request.status == "acknowledged"


def test_admin_pickup_ack_422(staff_client: StaffClientFactory, assert_error: AssertError) -> None:
    client, _ = staff_client(permissions=["pickup:operate"])

    assert_error(client.post(_request_url("abc", "/acknowledge")), 422, "validation_error")


def test_admin_pickup_ack_401(api_client: TestClient, assert_error: AssertError) -> None:
    assert_error(api_client.post(_request_url(uuid4(), "/acknowledge")), 401, "unauthenticated")


def test_admin_pickup_ack_403(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    ming, _ = _family(db_session)
    request = make_pickup_request(db_session, ming, service_date=_TODAY)
    client, _ = staff_client(permissions=["pickup:read"])

    assert_error(client.post(_request_url(request.id, "/acknowledge")), 403, "permission_denied")
    db_session.expire_all()
    assert request.status == "pending"


def test_admin_pickup_ack_409(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    ming, _ = _family(db_session)
    arrived = make_pickup_request(db_session, ming, service_date=_TODAY, status="arrived")
    client, _ = staff_client(permissions=["pickup:operate"])

    resp = client.post(_request_url(arrived.id, "/acknowledge"))

    assert_error(resp, 409, "invalid_pickup_status")
    assert resp.json()["error"]["details"] == {"current_status": "arrived"}
    assert_error(
        client.post(_request_url(uuid4(), "/acknowledge")), 404, "pickup_request_not_found"
    )


def test_admin_pickup_cancel_success(staff_client: StaffClientFactory, db_session: Session) -> None:
    ming, parent = _family(db_session)
    request = make_pickup_request(db_session, ming, service_date=_TODAY, requested_by=parent.id)
    client, _ = staff_client(permissions=["pickup:operate"])

    resp = client.post(_request_url(request.id, "/cancel"), json={"reason": "家長改搭校車"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "cancelled"
    assert body["cancel_reason"] == "家長改搭校車"
    assert body["cancelled_at"] is not None
    db_session.expire_all()
    assert request.status == "cancelled"
    notifications = list(
        db_session.execute(
            select(Notification).where(
                Notification.event == "pickup.cancelled",
                Notification.recipient_type == "parent",
                Notification.recipient_id == parent.id,
            )
        ).scalars()
    )
    assert len(notifications) == 1
    assert notifications[0].payload["request_id"] == str(request.id)
    # body 可省略
    other = make_pickup_request(db_session, make_student(db_session), service_date=_TODAY)
    db_session.commit()
    assert client.post(_request_url(other.id, "/cancel")).status_code == 200


def test_admin_pickup_cancel_422(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    ming, _ = _family(db_session)
    request = make_pickup_request(db_session, ming, service_date=_TODAY)
    client, _ = staff_client(permissions=["pickup:operate"])

    assert_error(
        client.post(_request_url(request.id, "/cancel"), json={"reason": "x" * 201}),
        422,
        "validation_error",
    )
    assert_error(client.post(_request_url("abc", "/cancel")), 422, "validation_error")
    db_session.expire_all()
    assert request.status == "pending"


def test_admin_pickup_cancel_401(api_client: TestClient, assert_error: AssertError) -> None:
    assert_error(api_client.post(_request_url(uuid4(), "/cancel")), 401, "unauthenticated")


def test_admin_pickup_cancel_403(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    ming, _ = _family(db_session)
    request = make_pickup_request(db_session, ming, service_date=_TODAY)
    client, _ = staff_client(permissions=["pickup:read"])

    assert_error(client.post(_request_url(request.id, "/cancel")), 403, "permission_denied")


def test_admin_pickup_cancel_409(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    ming, _ = _family(db_session)
    completed = make_pickup_request(db_session, ming, service_date=_TODAY, status="completed")
    client, _ = staff_client(permissions=["pickup:operate"])

    resp = client.post(_request_url(completed.id, "/cancel"))

    assert_error(resp, 409, "invalid_pickup_status")
    assert_error(client.post(_request_url(uuid4(), "/cancel")), 404, "pickup_request_not_found")


def test_admin_pickup_write_guard_registered(app: FastAPI) -> None:
    assert admin_routes_without_permission(app) == []
    paths = app.openapi()["paths"]
    assert "post" in paths[_REQUESTS]
    assert "post" in paths[_REQUESTS + "/{request_id}/acknowledge"]
    assert "post" in paths[_REQUESTS + "/{request_id}/cancel"]


# --- BACKEND-431：POST /requests/{id}/reply ---------------------------------------------------


def _parent_events(db: Session, event: str, parent_id: UUID) -> list[Notification]:
    return list(
        db.execute(
            select(Notification).where(
                Notification.event == event,
                Notification.recipient_type == "parent",
                Notification.recipient_id == parent_id,
            )
        ).scalars()
    )


def test_admin_pickup_reply_guard_registered(app: FastAPI) -> None:
    assert admin_routes_without_permission(app) == []
    assert "post" in app.openapi()["paths"][_REQUESTS + "/{request_id}/reply"]


def test_admin_pickup_reply_success(staff_client: StaffClientFactory, db_session: Session) -> None:
    ming, parent = _family(db_session)
    make_homework_progress(
        db_session, ming, service_date=_TODAY, overall_status="in_progress", ready_eta=time(17, 0)
    )
    request = make_pickup_request(db_session, ming, service_date=_TODAY, requested_by=parent.id)
    client, staff = staff_client(permissions=["pickup:operate"], display_name="林老師")

    resp = client.post(_request_url(request.id, "/reply"), json={"reply_ready_eta": "18:00"})

    assert resp.status_code == 200
    body = resp.json()
    assert (body["id"], body["status"]) == (str(request.id), "pending")
    assert body["reply_message"] == "預計 18:00 可接送"
    assert (body["reply_source"], body["reply_ready_eta"], body["replied_by_name"]) == (
        "staff",
        "18:00",
        "林老師",
    )
    assert body["needs_reply"] is False
    # 已 commit：重讀 DB；ETA 同交易寫回作業進度，發起的家長收到 pickup.replied
    db_session.expire_all()
    assert (request.reply_source, request.reply_ready_eta, request.replied_by) == (
        "staff",
        time(18, 0),
        staff.id,
    )
    progress = db_session.execute(
        select(HomeworkDailyProgress).where(
            HomeworkDailyProgress.student_id == ming.id,
            HomeworkDailyProgress.service_date == _TODAY,
        )
    ).scalar_one()
    assert (progress.ready_eta, progress.eta_updated_by) == (time(18, 0), staff.id)
    replied = _parent_events(db_session, "pickup.replied", parent.id)
    assert [n.payload["reply_message"] for n in replied] == ["預計 18:00 可接送"]

    # 只給訊息：ETA 清空、文案照給
    message_only = client.post(
        _request_url(request.id, "/reply"), json={"reply_message": "還在訂正，晚一點"}
    )

    assert message_only.status_code == 200
    assert (message_only.json()["reply_message"], message_only.json()["reply_ready_eta"]) == (
        "還在訂正，晚一點",
        None,
    )


def test_admin_pickup_reply_422(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    ming, _ = _family(db_session)
    request = make_pickup_request(db_session, ming, service_date=_TODAY)
    client, _ = staff_client(permissions=["pickup:operate"])

    for body in (
        {},
        {"reply_ready_eta": "25:00"},
        {"reply_ready_eta": "6pm"},
        {"reply_message": ""},
        {"reply_message": "x" * 201},
        {"reply_message": "好", "status": "acknowledged"},
    ):
        assert_error(
            client.post(_request_url(request.id, "/reply"), json=body), 422, "validation_error"
        )
    assert_error(client.post(_request_url(request.id, "/reply")), 422, "validation_error")
    assert_error(
        client.post(_request_url("abc", "/reply"), json={"reply_message": "好"}),
        422,
        "validation_error",
    )
    db_session.expire_all()
    assert (request.reply_source, request.reply_message) == (None, None)


def test_admin_pickup_reply_401(api_client: TestClient, assert_error: AssertError) -> None:
    resp = api_client.post(_request_url(uuid4(), "/reply"), json={"reply_message": "好"})

    assert_error(resp, 401, "unauthenticated")


def test_admin_pickup_reply_403(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    ming, _ = _family(db_session)
    request = make_pickup_request(db_session, ming, service_date=_TODAY)
    client, _ = staff_client(permissions=["pickup:read"])

    resp = client.post(_request_url(request.id, "/reply"), json={"reply_ready_eta": "18:00"})

    assert_error(resp, 403, "permission_denied")
    assert resp.json()["error"]["details"] == {"required": ["pickup:operate"]}
    db_session.expire_all()
    assert request.reply_source is None


def test_admin_pickup_reply_409(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    ming, _ = _family(db_session)
    completed = make_pickup_request(db_session, ming, service_date=_TODAY, status="completed")
    client, _ = staff_client(permissions=["pickup:operate"])

    resp = client.post(_request_url(completed.id, "/reply"), json={"reply_ready_eta": "18:00"})

    assert_error(resp, 409, "invalid_pickup_status")
    assert resp.json()["error"]["details"] == {"current_status": "completed"}
    assert_error(
        client.post(_request_url(uuid4(), "/reply"), json={"reply_ready_eta": "18:00"}),
        404,
        "pickup_request_not_found",
    )


# --- BACKEND-433：POST /requests/{id}/complete ------------------------------------------------


def _complete_url(request_id: object) -> str:
    return _request_url(request_id, "/complete")


def test_admin_pickup_complete_guard_registered(app: FastAPI) -> None:
    assert admin_routes_without_permission(app) == []
    assert "post" in app.openapi()["paths"][_REQUESTS + "/{request_id}/complete"]


def test_admin_pickup_complete_success(
    staff_client: StaffClientFactory, db_session: Session
) -> None:
    ming, mom = _family(db_session)
    dad = make_guardian(db_session, ming, name="王爸爸", relation="father")
    # 到班時間早於「現在」（台北 09:00），接送完成才會把出勤改 left
    make_attendance(
        db_session,
        ming,
        service_date=_TODAY,
        status="present",
        check_in_at=datetime(2026, 9, 1, 0, 30, tzinfo=UTC),
    )
    request = make_pickup_request(
        db_session, ming, service_date=_TODAY, status="arrived", requested_by=mom.id
    )
    client, staff = staff_client(permissions=["pickup:operate"], display_name="林老師")

    resp = client.post(
        _complete_url(request.id), json={"method": "guardian", "guardian_id": str(dad.id)}
    )

    assert resp.status_code == 200
    body = resp.json()
    assert (body["id"], body["status"], body["picked_up_by_name"]) == (
        str(request.id),
        "completed",
        "王爸爸",
    )
    assert (body["completion_method"], body["completed_by_name"]) == ("guardian", "林老師")
    # 已 commit：重讀 DB；出勤已 left、家長收到 pickup.completed
    db_session.expire_all()
    assert (request.status, request.completed_by, request.picked_up_by_guardian_id) == (
        "completed",
        staff.id,
        dad.id,
    )
    attendance = db_session.execute(
        select(StudentAttendance).where(
            StudentAttendance.student_id == ming.id, StudentAttendance.service_date == _TODAY
        )
    ).scalar_one()
    assert (attendance.status, attendance.check_out_source) == ("left", "pickup")
    completed = _parent_events(db_session, "pickup.completed", mom.id)
    assert [n.payload["picked_up_by"] for n in completed] == ["王爸爸"]


def test_admin_pickup_complete_override_audit(
    api_client: TestClient,
    app: FastAPI,
    db_session: Session,
    login_staff: Callable[[TestClient, StaffUser], None],
) -> None:
    ming, mom = _family(db_session)
    request = make_pickup_request(
        db_session, ming, service_date=_TODAY, status="acknowledged", requested_by=mom.id
    )
    supervisor = make_staff(db_session, permissions=["pickup:operate", "pickup:override"])
    db_session.commit()
    # 指定真實來源 IP 才能驗 request meta 有注入到 audit
    client = TestClient(app, base_url="http://testserver", client=("203.0.113.5", 50000))
    login_staff(client, supervisor)

    resp = client.post(
        _complete_url(request.id), json={"method": "override", "note": "家長來電確認由鄰居接"}
    )

    assert resp.status_code == 200
    assert (resp.json()["completion_method"], resp.json()["picked_up_by_name"]) == (
        "override",
        "老師確認交付",
    )
    log = db_session.execute(
        select(AuditLog).where(
            AuditLog.action == "pickup.override_complete", AuditLog.entity_id == str(request.id)
        )
    ).scalar_one()
    assert (log.actor_id, log.ip) == (supervisor.id, "203.0.113.5")
    assert log.after == {"status": "completed", "note": "家長來電確認由鄰居接"}


def test_admin_pickup_complete_422(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    ming, _ = _family(db_session)
    hua = make_student(db_session, name="陳小華")
    hua_mom = make_guardian(db_session, hua, name="陳媽媽")
    request = make_pickup_request(db_session, ming, service_date=_TODAY)
    client, _ = staff_client(permissions=["pickup:operate"])

    for body in (
        {"method": "guardian"},
        {"method": "override"},
        {"method": "code", "guardian_id": str(hua_mom.id)},
        {"method": "guardian", "guardian_id": "abc"},
        {"method": "override", "note": "x", "authorization_id": str(uuid4())},
        {},
    ):
        assert_error(client.post(_complete_url(request.id), json=body), 422, "validation_error")
    assert_error(
        client.post(_complete_url("abc"), json={"method": "override", "note": "x"}),
        422,
        "validation_error",
    )
    # 他人學生的監護人
    other = client.post(
        _complete_url(request.id), json={"method": "guardian", "guardian_id": str(hua_mom.id)}
    )
    assert_error(other, 422, "invalid_guardian")
    db_session.expire_all()
    assert request.status == "pending"


def test_admin_pickup_complete_401(api_client: TestClient, assert_error: AssertError) -> None:
    resp = api_client.post(_complete_url(uuid4()), json={"method": "override", "note": "x"})

    assert_error(resp, 401, "unauthenticated")


def test_admin_pickup_complete_403(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    ming, _ = _family(db_session)
    dad = make_guardian(db_session, ming, name="王爸爸", relation="father")
    request = make_pickup_request(db_session, ming, service_date=_TODAY)
    reader, _ = staff_client(permissions=["pickup:read"])
    operator, _ = staff_client(permissions=["pickup:operate"])

    resp = reader.post(
        _complete_url(request.id), json={"method": "guardian", "guardian_id": str(dad.id)}
    )

    assert_error(resp, 403, "permission_denied")
    assert resp.json()["error"]["details"] == {"required": ["pickup:operate"]}
    # pickup:operate 但強制完成另需 pickup:override（service 檢查）
    override = operator.post(
        _complete_url(request.id), json={"method": "override", "note": "家長來電確認"}
    )
    assert_error(override, 403, "permission_denied")
    assert override.json()["error"]["details"] == {"required": ["pickup:override"]}
    db_session.expire_all()
    assert request.status == "pending"


def test_admin_pickup_complete_409(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    ming, _ = _family(db_session)
    dad = make_guardian(db_session, ming, name="王爸爸", relation="father")
    grandma = make_guardian(
        db_session, ming, name="王奶奶", relation="grandparent", can_pickup=False
    )
    cancelled = make_pickup_request(db_session, ming, service_date=_TODAY, status="cancelled")
    pending = make_pickup_request(db_session, ming, service_date=_TODAY)
    client, _ = staff_client(permissions=["pickup:operate"])

    resp = client.post(
        _complete_url(cancelled.id), json={"method": "guardian", "guardian_id": str(dad.id)}
    )

    assert_error(resp, 409, "invalid_pickup_status")
    assert resp.json()["error"]["details"] == {"current_status": "cancelled"}
    assert_error(
        client.post(
            _complete_url(pending.id), json={"method": "guardian", "guardian_id": str(grandma.id)}
        ),
        409,
        "guardian_cannot_pickup",
    )
    assert_error(
        client.post(
            _complete_url(uuid4()), json={"method": "guardian", "guardian_id": str(dad.id)}
        ),
        404,
        "pickup_request_not_found",
    )
    db_session.expire_all()
    assert pending.status == "pending"


# --- BACKEND-437：POST /authorizations/{id}/verify --------------------------------------------

_AUTHS = f"{_URL}/authorizations"
# generate_pickup_code 的範圍是 100000~999999：重新產生的新碼不可能與舊碼相同
_OLD_CODE = "000000"


def _auth_url(auth_id: object, suffix: str) -> str:
    return f"{_AUTHS}/{auth_id}{suffix}"


def _reload_auth(db: Session, auth_id: object) -> PickupAuthorization:
    """跳過 identity map 重讀 DB 現值（handler 沒 commit 時，請求結束的 rollback 會退回）。"""
    return db.execute(
        select(PickupAuthorization)
        .where(PickupAuthorization.id == auth_id)
        .execution_options(populate_existing=True)
    ).scalar_one()


def test_admin_pickup_verify_guard_registered(app: FastAPI) -> None:
    assert admin_routes_without_permission(app) == []
    assert "post" in app.openapi()["paths"][_AUTHS + "/{auth_id}/verify"]


def test_admin_pickup_verify_success(staff_client: StaffClientFactory, db_session: Session) -> None:
    ming, mom = _family(db_session)
    # 到班時間早於「現在」（台北 09:00），核銷後出勤才會改 left
    make_attendance(
        db_session,
        ming,
        service_date=_TODAY,
        status="present",
        check_in_at=datetime(2026, 9, 1, 0, 30, tzinfo=UTC),
    )
    request = make_pickup_request(db_session, ming, service_date=_TODAY, requested_by=mom.id)
    auth = make_pickup_authorization(db_session, ming, service_date=_TODAY, code="135790")
    client, staff = staff_client(permissions=["pickup:operate"], display_name="林老師")

    resp = client.post(_auth_url(auth.id, "/verify"), json={"code": "135790"})

    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"authorization", "request"}
    authorization = body["authorization"]
    assert (authorization["id"], authorization["status"], authorization["verification_method"]) == (
        str(auth.id),
        "completed",
        "code",
    )
    assert authorization["verified_by_name"] == "林老師"
    assert (body["request"]["id"], body["request"]["status"]) == (str(request.id), "completed")
    assert body["request"]["completion_method"] == "code"
    assert "code_hash" not in resp.text
    assert "135790" not in resp.text
    # 已 commit：重讀 DB；授權與請求都完成、出勤改 left
    stored = _reload_auth(db_session, auth.id)
    assert (stored.status, stored.verification_method, stored.verified_by) == (
        "completed",
        "code",
        staff.id,
    )
    db_session.expire_all()
    assert (request.status, request.picked_up_by_authorization_id) == ("completed", auth.id)
    attendance = db_session.execute(
        select(StudentAttendance).where(
            StudentAttendance.student_id == ming.id, StudentAttendance.service_date == _TODAY
        )
    ).scalar_one()
    assert (attendance.status, attendance.check_out_source) == ("left", "pickup")


def test_admin_pickup_verify_422(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    ming, _ = _family(db_session)
    auth = make_pickup_authorization(db_session, ming, service_date=_TODAY)
    client, _ = staff_client(permissions=["pickup:operate"])

    for body in ({"code": ""}, {}, {"code": "1" * 21}, {"code": "123456", "method": "code"}):
        assert_error(client.post(_auth_url(auth.id, "/verify"), json=body), 422, "validation_error")
    assert_error(
        client.post(_auth_url("abc", "/verify"), json={"code": "123456"}),
        422,
        "validation_error",
    )
    # 格式錯誤不計入連錯
    assert (_reload_auth(db_session, auth.id).code_attempts, auth.status) == (0, "active")


def test_admin_pickup_verify_401(api_client: TestClient, assert_error: AssertError) -> None:
    resp = api_client.post(_auth_url(uuid4(), "/verify"), json={"code": "123456"})

    assert_error(resp, 401, "unauthenticated")


def test_admin_pickup_verify_403(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    ming, _ = _family(db_session)
    auth = make_pickup_authorization(db_session, ming, service_date=_TODAY, code="135790")
    client, _ = staff_client(permissions=["pickup:read"])

    resp = client.post(_auth_url(auth.id, "/verify"), json={"code": "246802"})

    assert_error(resp, 403, "permission_denied")
    assert resp.json()["error"]["details"] == {"required": ["pickup:operate"]}
    stored = _reload_auth(db_session, auth.id)
    assert (stored.status, stored.code_attempts) == ("active", 0)


def test_admin_pickup_verify_mismatch_persists(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    ming, _ = _family(db_session)
    auth = make_pickup_authorization(db_session, ming, service_date=_TODAY, code="135790")
    client, _ = staff_client(permissions=["pickup:operate", "pickup:read"])

    resp = client.post(_auth_url(auth.id, "/verify"), json={"code": "246802"})

    assert_error(resp, 400, "pickup_code_mismatch")
    assert resp.json()["error"]["details"] == {"remaining_attempts": 4}
    # 錯碼累計在回 400 之前已 commit：請求結束的 rollback 不會把它退回
    stored = _reload_auth(db_session, auth.id)
    assert (stored.code_attempts, stored.code_locked_at, stored.status) == (1, None, "active")
    # 新的請求（核驗清單）也看得到累計
    listed = client.get(_AUTHS).json()
    assert [(a["id"], a["code_attempts"], a["locked"]) for a in listed] == [
        (str(auth.id), 1, False)
    ]


def test_admin_pickup_verify_lock_on_fifth(
    staff_client: StaffClientFactory,
    db_session: Session,
    assert_error: AssertError,
    fake_clock: FakeClock,
) -> None:
    ming, _ = _family(db_session)
    auth = make_pickup_authorization(db_session, ming, service_date=_TODAY, code="135790")
    client, _ = staff_client(permissions=["pickup:operate"])

    remaining = []
    for _ in range(4):
        resp = client.post(_auth_url(auth.id, "/verify"), json={"code": "246802"})
        assert_error(resp, 400, "pickup_code_mismatch")
        remaining.append(resp.json()["error"]["details"]["remaining_attempts"])
    fifth = client.post(_auth_url(auth.id, "/verify"), json={"code": "246802"})
    # 鎖定後即使正確碼也拒絕（不自動解鎖）
    sixth = client.post(_auth_url(auth.id, "/verify"), json={"code": "135790"})

    assert remaining == [4, 3, 2, 1]
    assert_error(fifth, 409, "pickup_code_locked")
    assert_error(sixth, 409, "pickup_code_locked")
    stored = _reload_auth(db_session, auth.id)
    assert (stored.code_attempts, stored.code_locked_at, stored.status) == (
        5,
        fake_clock.now(),
        "active",
    )


def test_admin_pickup_verify_after_regenerate(
    staff_client: StaffClientFactory,
    parent_client: ParentClientFactory,
    db_session: Session,
    assert_error: AssertError,
) -> None:
    parent_api, parent = parent_client()
    ming = make_student(db_session, name="王小明")
    make_guardian(db_session, ming, parent=parent)
    # 已錯 3 次：重新產生會把連錯次數歸零
    auth = make_pickup_authorization(
        db_session, ming, service_date=_TODAY, code=_OLD_CODE, code_attempts=3
    )
    client, _ = staff_client(permissions=["pickup:operate"])

    regenerated = parent_api.post(f"/api/parent/pickup-authorizations/{auth.id}/regenerate-code")
    assert regenerated.status_code == 200
    new_code = regenerated.json()["code"]

    old = client.post(_auth_url(auth.id, "/verify"), json={"code": _OLD_CODE})

    assert_error(old, 400, "pickup_code_mismatch")
    assert old.json()["error"]["details"] == {"remaining_attempts": 4}

    new = client.post(_auth_url(auth.id, "/verify"), json={"code": new_code})

    assert new.status_code == 200
    assert new.json()["authorization"]["status"] == "completed"
    stored = _reload_auth(db_session, auth.id)
    assert (stored.status, stored.verification_method, stored.code_attempts) == (
        "completed",
        "code",
        1,
    )


# --- BACKEND-438：POST /authorizations/{id}/confirm-visual-match ------------------------------


def _audits(db: Session, action: str, entity_id: object) -> list[AuditLog]:
    return list(
        db.execute(
            select(AuditLog).where(AuditLog.action == action, AuditLog.entity_id == str(entity_id))
        ).scalars()
    )


def test_admin_pickup_visual_guard_registered(app: FastAPI) -> None:
    assert admin_routes_without_permission(app) == []
    assert "post" in app.openapi()["paths"][_AUTHS + "/{auth_id}/confirm-visual-match"]


def test_admin_pickup_visual_success(
    api_client: TestClient,
    app: FastAPI,
    db_session: Session,
    login_staff: Callable[[TestClient, StaffUser], None],
) -> None:
    ming, mom = _family(db_session)
    request = make_pickup_request(db_session, ming, service_date=_TODAY, requested_by=mom.id)
    person = make_pickup_person(
        db_session, ming, name="李阿姨", photo_path=build_object_path(uuid4(), "jpg")
    )
    with_photo = make_pickup_authorization(db_session, ming, service_date=_TODAY, person=person)
    hua = make_student(db_session, name="陳小華")
    # 一次性代理人（沒有常用接送人、沒有照片）
    one_off = make_pickup_authorization(
        db_session, hua, service_date=_TODAY, proxy_name="王叔叔", code="654321"
    )
    staff = make_staff(db_session, permissions=["pickup:operate"], display_name="林老師")
    db_session.commit()
    # 指定真實來源 IP 才能驗 request meta 有注入到 audit
    client = TestClient(app, base_url="http://testserver", client=("203.0.113.5", 50000))
    login_staff(client, staff)

    # 有照片：body 可省略
    resp = client.post(_auth_url(with_photo.id, "/confirm-visual-match"))

    assert resp.status_code == 200
    body = resp.json()
    assert (body["authorization"]["id"], body["authorization"]["status"]) == (
        str(with_photo.id),
        "completed",
    )
    assert body["authorization"]["verification_method"] == "visual_match"
    assert body["authorization"]["verified_by_name"] == "林老師"
    assert (body["request"]["id"], body["request"]["completion_method"]) == (
        str(request.id),
        "visual_match",
    )
    assert body["request"]["picked_up_by_name"] == "李阿姨"

    # 沒有照片：員工核對證件後確認
    no_photo = client.post(
        _auth_url(one_off.id, "/confirm-visual-match"), json={"note": "已核對身分證"}
    )

    assert no_photo.status_code == 200
    assert (
        no_photo.json()["authorization"]["status"],
        no_photo.json()["authorization"]["verification_method"],
    ) == ("completed", "visual_match")
    assert (no_photo.json()["request"]["source"], no_photo.json()["request"]["status"]) == (
        "proxy",
        "completed",
    )
    # 已 commit：重讀 DB；兩筆都寫了 audit（記錄來源 IP、是否有照片、備註）
    stored = [_reload_auth(db_session, a.id) for a in (with_photo, one_off)]
    assert [(a.status, a.verified_by) for a in stored] == [("completed", staff.id)] * 2
    [photo_log] = _audits(db_session, "pickup.visual_match", with_photo.id)
    [id_log] = _audits(db_session, "pickup.visual_match", one_off.id)
    assert (photo_log.actor_id, photo_log.ip, photo_log.after) == (
        staff.id,
        "203.0.113.5",
        {"status": "completed", "has_photo": True, "note": None},
    )
    assert id_log.after == {"status": "completed", "has_photo": False, "note": "已核對身分證"}


def test_admin_pickup_visual_422(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    ming, _ = _family(db_session)
    auth = make_pickup_authorization(db_session, ming, service_date=_TODAY)
    client, _ = staff_client(permissions=["pickup:operate"])

    assert_error(client.post(_auth_url("abc", "/confirm-visual-match")), 422, "validation_error")
    for body in ({"note": "x" * 201}, {"note": "已核對", "method": "visual_match"}):
        assert_error(
            client.post(_auth_url(auth.id, "/confirm-visual-match"), json=body),
            422,
            "validation_error",
        )
    assert _reload_auth(db_session, auth.id).status == "active"


def test_admin_pickup_visual_401(api_client: TestClient, assert_error: AssertError) -> None:
    resp = api_client.post(_auth_url(uuid4(), "/confirm-visual-match"))

    assert_error(resp, 401, "unauthenticated")


def test_admin_pickup_visual_403(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    ming, _ = _family(db_session)
    auth = make_pickup_authorization(db_session, ming, service_date=_TODAY)
    client, _ = staff_client(permissions=["pickup:read"])

    resp = client.post(_auth_url(auth.id, "/confirm-visual-match"))

    assert_error(resp, 403, "permission_denied")
    assert resp.json()["error"]["details"] == {"required": ["pickup:operate"]}
    assert _reload_auth(db_session, auth.id).status == "active"
    assert _audits(db_session, "pickup.visual_match", auth.id) == []


def test_admin_pickup_visual_409(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    ming, _ = _family(db_session)
    locked = make_pickup_authorization(db_session, ming, service_date=_TODAY, code_attempts=5)
    completed = make_pickup_authorization(
        db_session, ming, service_date=_TODAY, status="completed", code="654321"
    )
    client, _ = staff_client(permissions=["pickup:operate"])

    locked_resp = client.post(_auth_url(locked.id, "/confirm-visual-match"))
    completed_resp = client.post(_auth_url(completed.id, "/confirm-visual-match"))

    assert_error(locked_resp, 409, "pickup_code_locked")
    assert_error(completed_resp, 409, "authorization_not_active")
    assert_error(
        client.post(_auth_url(uuid4(), "/confirm-visual-match")),
        404,
        "pickup_authorization_not_found",
    )
    assert _reload_auth(db_session, locked.id).status == "active"
    assert _audits(db_session, "pickup.visual_match", locked.id) == []
