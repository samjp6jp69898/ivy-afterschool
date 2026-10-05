"""BACKEND-385：GET /api/admin/homework/board。"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import date, time
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.account import StaffUser
from app.services.settings_service import clear_settings_cache
from tests.support.factories import (
    make_attendance,
    make_class,
    make_homework_item,
    make_homework_progress,
    make_student,
)
from tests.support.route_audit import admin_routes_without_permission

_URL = "/api/admin/homework/board"
_DAY = date(2026, 9, 1)
StaffClientFactory = Callable[..., tuple[TestClient, StaffUser]]
AssertError = Callable[..., None]


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    clear_settings_cache()
    yield
    clear_settings_cache()


def test_admin_homework_board_success(
    staff_client: StaffClientFactory, db_session: Session
) -> None:
    class_a = make_class(db_session, name="A班")
    ming = make_student(db_session, name="王小明", student_no="H-001", class_=class_a)
    hua = make_student(db_session, name="陳小華", student_no="H-002", class_=class_a)
    make_homework_item(db_session, ming, service_date=_DAY, title="國語生字", sort_order=10)
    make_homework_item(
        db_session, ming, service_date=_DAY, title="數學習作", status="done", sort_order=20
    )
    make_homework_progress(
        db_session, ming, service_date=_DAY, overall_status="in_progress", ready_eta=time(17, 30)
    )
    make_attendance(db_session, ming, service_date=_DAY, status="present")
    make_attendance(db_session, hua, service_date=_DAY)
    client, _ = staff_client(permissions=["homework:read"])

    resp = client.get(_URL, params={"date": "2026-09-01", "class_id": str(class_a.id)})

    assert resp.status_code == 200
    body = resp.json()
    assert body["date"] == "2026-09-01"
    assert set(body) == {"date", "window", "summary", "students"}
    assert [s["name"] for s in body["students"]] == ["王小明", "陳小華"]
    ming_card = body["students"][0]
    assert ming_card["attendance_status"] == "present"
    assert [i["title"] for i in ming_card["items"]] == ["國語生字", "數學習作"]
    assert ming_card["progress"]["overall_status"] == "in_progress"
    assert ming_card["progress"]["ready_eta"] == "17:30"
    assert body["students"][1]["items"] == []
    # 不給 date → 今天（fake_clock 台北 2026-09-01）
    assert client.get(_URL, params={"class_id": str(class_a.id)}).json()["date"] == "2026-09-01"


def test_admin_homework_board_422(
    staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    client, _ = staff_client(permissions=["homework:read"])

    assert_error(client.get(_URL, params={"date": "bad"}), 422, "validation_error")
    assert_error(client.get(_URL, params={"class_id": "abc"}), 422, "validation_error")
    assert_error(client.get(_URL, params={"foo": "bar"}), 422, "validation_error")


def test_admin_homework_board_401(api_client: TestClient, assert_error: AssertError) -> None:
    assert_error(api_client.get(_URL), 401, "unauthenticated")


def test_admin_homework_board_403(
    staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    client, _ = staff_client(permissions=["attendance:read"])

    resp = client.get(_URL)

    assert_error(resp, 403, "permission_denied")
    assert resp.json()["error"]["details"] == {"required": ["homework:read"]}


def test_admin_homework_board_unknown_class(
    staff_client: StaffClientFactory, db_session: Session
) -> None:
    make_student(db_session)
    client, _ = staff_client(permissions=["homework:read"])

    resp = client.get(_URL, params={"class_id": str(uuid4())})

    assert resp.status_code == 200
    assert resp.json()["students"] == []


def test_admin_homework_board_guard_registered(app: FastAPI) -> None:
    assert admin_routes_without_permission(app) == []
    assert "get" in app.openapi()["paths"][_URL]
