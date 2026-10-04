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


_CREATE_PAYLOADS: dict[str, dict[str, object]] = {
    "subjects": {"name": "書法"},
    "exam-types": {"name": "月考"},
    "schools": {"name": "仁愛國小", "short_name": "仁愛"},
    "closed-days": {"date": "2026-10-10", "reason": "國慶日"},
}


@pytest.mark.parametrize("resource", RESOURCES)
def test_admin_reference_create_success(staff_client: StaffClientFactory, resource: str) -> None:
    client, _ = staff_client(permissions=["settings:write"])
    payload = _CREATE_PAYLOADS[resource]

    resp = client.post(f"/api/admin/{resource}", json=payload)

    assert resp.status_code == 201
    body = resp.json()
    assert "id" in body
    for key, value in payload.items():
        assert body[key] == value
    if resource == "subjects":
        assert body["is_active"] is True
        assert body["sort_order"] == 0
    # 寫入後 GET 看得到
    reader, _ = staff_client(permissions=["settings:read"])
    listed = reader.get(f"/api/admin/{resource}").json()
    assert body["id"] in [item["id"] for item in listed]


@pytest.mark.parametrize("resource", RESOURCES)
def test_admin_reference_create_422(
    staff_client: StaffClientFactory, assert_error: AssertError, resource: str
) -> None:
    client, _ = staff_client(permissions=["settings:write"])
    url = f"/api/admin/{resource}"
    bad = {"date": "2026-13-01"} if resource == "closed-days" else {"name": ""}

    assert_error(client.post(url, json=bad), 422, "validation_error")
    extra = {**_CREATE_PAYLOADS[resource], "foo": 1}
    assert_error(client.post(url, json=extra), 422, "validation_error")
    assert_error(client.post(url, json={}), 422, "validation_error")


@pytest.mark.parametrize("resource", RESOURCES)
def test_admin_reference_create_401(
    api_client: TestClient, assert_error: AssertError, resource: str
) -> None:
    resp = api_client.post(f"/api/admin/{resource}", json=_CREATE_PAYLOADS[resource])

    assert_error(resp, 401, "unauthenticated")


@pytest.mark.parametrize("resource", RESOURCES)
@pytest.mark.parametrize("permission", ["settings:read", "students:read", "exams:read"])
def test_admin_reference_create_403(
    staff_client: StaffClientFactory,
    assert_error: AssertError,
    resource: str,
    permission: str,
) -> None:
    client, _ = staff_client(permissions=[permission])

    resp = client.post(f"/api/admin/{resource}", json=_CREATE_PAYLOADS[resource])

    assert_error(resp, 403, "permission_denied")
    assert resp.json()["error"]["details"] == {"required": ["settings:write"]}


def test_admin_reference_create_409(
    staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    client, _ = staff_client(permissions=["settings:write"])

    exam_type = client.post("/api/admin/exam-types", json={"name": "段考"})
    subject = client.post("/api/admin/subjects", json={"name": " 數學 "})
    first_day = client.post("/api/admin/closed-days", json={"date": "2026-10-10"})
    second_day = client.post("/api/admin/closed-days", json={"date": "2026-10-10"})
    school = client.post("/api/admin/schools", json={"name": "ABC國小"})
    school_dup = client.post("/api/admin/schools", json={"name": "abc國小"})

    assert_error(exam_type, 409, "exam_type_name_taken")
    assert_error(subject, 409, "subject_name_taken")
    assert first_day.status_code == 201
    assert_error(second_day, 409, "closed_day_exists")
    assert school.status_code == 201
    assert_error(school_dup, 409, "school_name_taken")


def test_admin_reference_create_guard_registered(app: FastAPI) -> None:
    assert admin_routes_without_permission(app) == []
    paths = app.openapi()["paths"]
    for resource in RESOURCES:
        assert "post" in paths[f"/api/admin/{resource}"]
