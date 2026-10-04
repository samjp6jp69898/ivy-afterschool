"""BACKEND-141：GET /api/admin/classes。"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.account import StaffUser
from tests.support.factories import make_class, make_class_staff, make_student
from tests.support.route_audit import admin_routes_without_permission

_URL = "/api/admin/classes"
StaffClientFactory = Callable[..., tuple[TestClient, StaffUser]]
AssertError = Callable[..., None]


def test_admin_classes_list_success(staff_client: StaffClientFactory, db_session: Session) -> None:
    klass = make_class(db_session, name="A班", grade_levels=(1, 2), academic_year=115)
    make_student(db_session, class_=klass)
    make_student(db_session, class_=klass)
    client, staff = staff_client(permissions=["classes:read"])
    make_class_staff(db_session, klass, staff, role="lead")
    db_session.commit()

    resp = client.get(_URL, params={"academic_year": 115})

    assert resp.status_code == 200
    item = next(c for c in resp.json() if c["id"] == str(klass.id))
    assert item["name"] == "A班"
    assert item["student_count"] == 2
    assert item["grade_levels"] == [1, 2]
    assert [s["display_name"] for s in item["staff"]] == [staff.display_name]
    assert set(item) == {
        "id",
        "name",
        "grade_levels",
        "academic_year",
        "sort_order",
        "archived_at",
        "student_count",
        "staff",
    }


def test_admin_classes_list_mine(staff_client: StaffClientFactory, db_session: Session) -> None:
    mine = make_class(db_session, name="我的班")
    make_class(db_session, name="別人的班")
    client, staff = staff_client(permissions=["classes:read"])
    make_class_staff(db_session, mine, staff, role="assistant")
    db_session.commit()

    resp = client.get(_URL, params={"mine": "true"})

    assert [c["id"] for c in resp.json()] == [str(mine.id)]
    assert len(client.get(_URL).json()) >= 2


def test_admin_classes_list_archived(staff_client: StaffClientFactory, db_session: Session) -> None:
    archived = make_class(db_session, name="舊班", archived=True)
    client, _ = staff_client(permissions=["classes:read"])

    default = {c["id"] for c in client.get(_URL).json()}
    with_archived = {c["id"] for c in client.get(_URL, params={"include_archived": "true"}).json()}

    assert str(archived.id) not in default
    assert str(archived.id) in with_archived


def test_admin_classes_list_422(
    staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    client, _ = staff_client(permissions=["classes:read"])

    for params in ({"academic_year": "abc"}, {"mine": "maybe"}, {"foo": "bar"}):
        assert_error(client.get(_URL, params=params), 422, "validation_error")


def test_admin_classes_list_401(api_client: TestClient, assert_error: AssertError) -> None:
    assert_error(api_client.get(_URL), 401, "unauthenticated")


def test_admin_classes_list_403(
    staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    client, _ = staff_client(permissions=["students:read"])

    resp = client.get(_URL)

    assert_error(resp, 403, "permission_denied")
    assert resp.json()["error"]["details"] == {"required": ["classes:read"]}


def test_admin_classes_list_guard_registered(app: FastAPI) -> None:
    assert admin_routes_without_permission(app) == []
    assert "/api/admin/classes" in app.openapi()["paths"]
