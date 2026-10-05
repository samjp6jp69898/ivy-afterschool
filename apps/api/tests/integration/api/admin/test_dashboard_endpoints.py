"""BACKEND-492：GET /api/admin/dashboard/today。

計數涵蓋整個 DB（儀表板沒有篩選），測資只在 db_session 交易內；營業時段讀 seed 預設（週一到週五）。
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import date

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.account import StaffUser
from app.services.settings_service import clear_settings_cache
from tests.support.factories import make_attendance, make_student
from tests.support.route_audit import admin_routes_without_permission

_URL = "/api/admin/dashboard/today"
_TODAY = date(2026, 9, 8)  # 週二
_CLOCK = "2026-09-08T15:00:00+08:00"
_SUNDAY_CLOCK = "2026-09-06T15:00:00+08:00"
StaffClientFactory = Callable[..., tuple[TestClient, StaffUser]]
AssertError = Callable[..., None]


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    clear_settings_cache()
    yield
    clear_settings_cache()


@pytest.mark.clock(_CLOCK)
def test_admin_dashboard_success(staff_client: StaffClientFactory, db_session: Session) -> None:
    students = [make_student(db_session, name=f"學生{i}") for i in range(3)]
    make_attendance(db_session, students[0], service_date=_TODAY, status="present")
    make_attendance(db_session, students[1], service_date=_TODAY, status="present")
    client, _ = staff_client(permissions=["dashboard:read"])

    resp = client.get(_URL)

    assert resp.status_code == 200
    body = resp.json()
    assert body["date"] == "2026-09-08"
    assert body["is_service_day"] is True
    assert body["attendance"]["present"] == 2
    assert body["attendance"]["arrived"] == 2
    assert body["attendance"]["expected_total"] == 3
    assert body["attendance"]["not_arrived"] == 1
    assert body["homework"]["total"] == 2
    assert set(body) == {
        "date",
        "is_service_day",
        "attendance",
        "pickup",
        "homework",
        "recent_leaves",
    }
    assert set(body["pickup"]) == {"open", "needs_reply", "arrived", "completed"}
    assert set(body["homework"]) == {
        "total",
        "done",
        "in_progress",
        "not_started",
        "completion_rate",
    }
    assert body["recent_leaves"] == []


@pytest.mark.clock(_CLOCK)
def test_admin_dashboard_422(staff_client: StaffClientFactory, assert_error: AssertError) -> None:
    """無 query / body 參數：未宣告的 query 被忽略仍 200；不支援的方法 405。"""
    client, _ = staff_client(permissions=["dashboard:read"])

    assert client.get(_URL, params={"date": "x"}).status_code == 200
    assert_error(client.post(_URL), 405, "method_not_allowed")


def test_admin_dashboard_401(api_client: TestClient, assert_error: AssertError) -> None:
    assert_error(api_client.get(_URL), 401, "unauthenticated")


def test_admin_dashboard_403(staff_client: StaffClientFactory, assert_error: AssertError) -> None:
    client, _ = staff_client(permissions=["students:read"])

    resp = client.get(_URL)

    assert_error(resp, 403, "permission_denied")
    assert resp.json()["error"]["details"] == {"required": ["dashboard:read"]}


@pytest.mark.clock(_SUNDAY_CLOCK)
def test_admin_dashboard_non_service_day(
    staff_client: StaffClientFactory, db_session: Session
) -> None:
    make_student(db_session)  # 非營業日：沒有出勤列的學生不計入應到
    client, _ = staff_client(permissions=["dashboard:read"])

    resp = client.get(_URL)

    assert resp.status_code == 200
    body = resp.json()
    assert body["date"] == "2026-09-06"
    assert body["is_service_day"] is False
    assert body["attendance"]["expected_total"] == 0
    assert body["attendance"]["not_arrived"] == 0


def test_admin_dashboard_guard_registered(app: FastAPI) -> None:
    assert admin_routes_without_permission(app) == []
    assert "get" in app.openapi()["paths"][_URL]
