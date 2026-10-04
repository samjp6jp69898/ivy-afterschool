"""BACKEND-119：GET /api/admin/{subjects|exam-types|schools|closed-days}。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.account import StaffUser
from app.models.reference import ClosedDay, School, Subject
from tests.support.factories import make_staff
from tests.support.route_audit import admin_routes_without_permission

RESOURCES = ["subjects", "exam-types", "schools", "closed-days"]
StaffClientFactory = Callable[..., tuple[TestClient, StaffUser]]
AssertError = Callable[..., None]


@pytest.mark.parametrize("resource", RESOURCES)
def test_admin_reference_list_success(staff_client: StaffClientFactory, resource: str) -> None:
    client, _ = staff_client(permissions=["settings:read"])

    resp = client.get(f"/api/admin/{resource}")

    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    if resource == "subjects":
        assert "國語" in [item["name"] for item in body]
        assert set(body[0]) == {"id", "name", "sort_order", "is_active"}
    if resource == "exam-types":
        assert "段考" in [item["name"] for item in body]


@pytest.mark.parametrize("permission", ["students:read", "exams:read", "homework:read"])
def test_admin_reference_list_any_of_permissions(
    staff_client: StaffClientFactory, permission: str
) -> None:
    client, _ = staff_client(permissions=[permission])

    assert client.get("/api/admin/subjects").status_code == 200


def test_admin_reference_list_tutor_allowed(
    api_client: TestClient,
    db_session: Session,
    login_staff: Callable[[TestClient, StaffUser], None],
) -> None:
    tutor = make_staff(db_session, role_code="tutor")
    db_session.commit()
    login_staff(api_client, tutor)

    resp = api_client.get("/api/admin/subjects")

    assert resp.status_code == 200
    assert len(resp.json()) >= 5


def test_admin_reference_list_filters(
    staff_client: StaffClientFactory, db_session: Session
) -> None:
    db_session.add(Subject(name="書法", sort_order=99, is_active=False))
    db_session.add(School(name="仁愛國小"))
    for d in (date(2026, 10, 10), date(2026, 12, 25), date(2027, 1, 1)):
        db_session.add(ClosedDay(date=d, reason="測試"))
    db_session.flush()
    client, _ = staff_client(permissions=["settings:read"])

    everything = [i["name"] for i in client.get("/api/admin/subjects").json()]
    active = [i["name"] for i in client.get("/api/admin/subjects?active_only=true").json()]
    days = client.get("/api/admin/closed-days?date_from=2026-10-01&date_to=2026-12-31").json()

    assert everything[-1] == "書法"
    assert "書法" not in active
    assert [d["date"] for d in days] == ["2026-12-25", "2026-10-10"]
    assert "仁愛國小" in [s["name"] for s in client.get("/api/admin/schools").json()]


@pytest.mark.parametrize("resource", RESOURCES)
def test_admin_reference_list_422(
    staff_client: StaffClientFactory, assert_error: AssertError, resource: str
) -> None:
    client, _ = staff_client(permissions=["settings:read"])

    bad_date = client.get(f"/api/admin/{resource}", params={"date_from": "2026-13-01"})
    unknown = client.get(f"/api/admin/{resource}", params={"foo": "bar"})
    bad_bool = client.get(f"/api/admin/{resource}", params={"active_only": "maybe"})

    for resp in (bad_date, unknown, bad_bool):
        assert_error(resp, 422, "validation_error")


@pytest.mark.parametrize("resource", RESOURCES)
def test_admin_reference_list_401(
    api_client: TestClient, assert_error: AssertError, resource: str
) -> None:
    assert_error(api_client.get(f"/api/admin/{resource}"), 401, "unauthenticated")


@pytest.mark.parametrize("resource", RESOURCES)
def test_admin_reference_list_403(
    staff_client: StaffClientFactory, assert_error: AssertError, resource: str
) -> None:
    client, _ = staff_client(permissions=["audit:read"])

    resp = client.get(f"/api/admin/{resource}")

    assert_error(resp, 403, "permission_denied")
    assert set(resp.json()["error"]["details"]["required"]) == {
        "settings:read",
        "students:read",
        "exams:read",
        "homework:read",
    }


def test_admin_reference_list_guard_registered(app: FastAPI) -> None:
    assert admin_routes_without_permission(app) == []
    paths = app.openapi()["paths"]
    for resource in RESOURCES:
        assert f"/api/admin/{resource}" in paths
