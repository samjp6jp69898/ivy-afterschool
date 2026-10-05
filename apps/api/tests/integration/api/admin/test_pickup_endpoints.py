"""BACKEND-429：GET /api/admin/pickup/queue。
BACKEND-435：GET /api/admin/pickup/roster（POS 學生卡）。
BACKEND-436：GET /api/admin/pickup/authorizations（代理接送核驗清單）。

fake_clock 預設 2026-09-01 01:00 UTC（台北 09:00）；接送請求 / 授權的 service_date 用台北「今天」。
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import date
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.account import StaffUser
from app.services.settings_service import clear_settings_cache
from tests.support.factories import (
    make_class,
    make_pickup_authorization,
    make_pickup_request,
    make_student,
)
from tests.support.route_audit import admin_routes_without_permission

_URL = "/api/admin/pickup"
_TODAY = date(2026, 9, 1)
StaffClientFactory = Callable[..., tuple[TestClient, StaffUser]]
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
