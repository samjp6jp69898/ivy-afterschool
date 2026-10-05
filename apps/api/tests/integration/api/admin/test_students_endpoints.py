"""BACKEND-159：GET /api/admin/students。
BACKEND-161：GET /api/admin/students/{student_id}（students:read；sensitive 另需
students:sensitive）。
"""

from __future__ import annotations

from collections.abc import Callable
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.crypto import encrypt_bytes
from app.models.account import StaffUser
from app.models.students import Student
from app.services.students.id_number import id_number_hmac
from tests.support.factories import make_class, make_guardian, make_school, make_student
from tests.support.route_audit import admin_routes_without_permission

_URL = "/api/admin/students"
StaffClientFactory = Callable[..., tuple[TestClient, StaffUser]]
AssertError = Callable[..., None]
_ID_NUMBER = "A123456789"


def test_admin_students_list_success(staff_client: StaffClientFactory, db_session: Session) -> None:
    klass = make_class(db_session, name="三年甲班")
    school = make_school(db_session, name="新生國小")
    make_student(db_session, name="王小明", student_no="S115001", class_=klass, school=school)
    make_student(db_session, name="陳小華", student_no="S115002")
    client, _ = staff_client(permissions=["students:read"])

    resp = client.get(_URL, params={"q": "王"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["name"] == "王小明"
    assert item["student_no"] == "S115001"
    assert item["class"]["name"] == "三年甲班"
    assert "class_" not in item
    assert item["school"]["name"] == "新生國小"
    assert set(item) == {
        "id",
        "student_no",
        "name",
        "gender",
        "grade_level",
        "school",
        "school_class",
        "class",
        "status",
        "archived_at",
    }


def test_admin_students_list_filters_and_paging(
    staff_client: StaffClientFactory, db_session: Session
) -> None:
    klass = make_class(db_session)
    for n in range(25):
        make_student(db_session, class_=klass, student_no=f"P{n:03d}", grade_level=3)
    make_student(db_session, class_=klass, student_no="ARCH", archived=True)
    client, _ = staff_client(permissions=["students:read"])

    page3 = client.get(_URL, params={"class_id": str(klass.id), "page": 3, "page_size": 10}).json()
    with_archived = client.get(
        _URL, params={"class_id": str(klass.id), "include_archived": "true", "page_size": 100}
    ).json()

    assert page3["total"] == 25
    assert [i["student_no"] for i in page3["items"]] == [f"P{n:03d}" for n in range(20, 25)]
    assert with_archived["total"] == 26
    assert (
        client.get(_URL, params={"class_id": str(klass.id), "grade_level": 4}).json()["total"] == 0
    )


def test_admin_students_list_422(
    staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    client, _ = staff_client(permissions=["students:read"])

    for params in (
        {"grade_level": 9},
        {"status": "graduated"},
        {"class_id": "not-a-uuid"},
        {"q": "x" * 51},
        {"include_archived": "maybe"},
        {"page": 0},
        {"unknown": "1"},
    ):
        assert_error(client.get(_URL, params=params), 422, "validation_error")


def test_admin_students_list_401(api_client: TestClient, assert_error: AssertError) -> None:
    assert_error(api_client.get(_URL), 401, "unauthenticated")


def test_admin_students_list_403(
    staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    client, _ = staff_client(permissions=["classes:read"])

    resp = client.get(_URL)

    assert_error(resp, 403, "permission_denied")
    assert resp.json()["error"]["details"] == {"required": ["students:read"]}


def test_admin_students_list_no_sensitive(
    staff_client: StaffClientFactory, db_session: Session
) -> None:
    student = make_student(db_session, name="王小明")
    student.id_number_enc = b"\x01encrypted-id"
    student.id_number_hmac = "a" * 64
    student.health_note_enc = b"\x01encrypted-health"
    db_session.flush()
    client, _ = staff_client(permissions=["students:read", "students:sensitive"])

    resp = client.get(_URL, params={"q": "王小明"})

    assert resp.status_code == 200
    for leaked in (_ID_NUMBER, "id_number", "health_note", "encrypted", "hmac", "sensitive"):
        assert leaked not in resp.text


def test_admin_students_list_guard_registered(app: FastAPI) -> None:
    assert admin_routes_without_permission(app) == []
    assert "/api/admin/students" in app.openapi()["paths"]


# --- BACKEND-161：GET /api/admin/students/{student_id} --------------------------------------------


def _with_sensitive(db: Session) -> Student:
    student = make_student(db, name="王小明", student_no="S115001")
    student.id_number_enc = encrypt_bytes(_ID_NUMBER)
    student.id_number_hmac = id_number_hmac(_ID_NUMBER)
    student.health_note_enc = encrypt_bytes("對花生過敏")
    student.photo_path = f"{student.id}/{'ab' * 16}.jpg"
    make_guardian(db, student, name="王媽媽", is_primary=True)
    db.commit()
    return student


def test_admin_students_get_sensitive(
    staff_client: StaffClientFactory, db_session: Session
) -> None:
    student = _with_sensitive(db_session)
    client, _ = staff_client(permissions=["students:read", "students:sensitive"])

    resp = client.get(f"{_URL}/{student.id}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == str(student.id)
    assert body["name"] == "王小明"
    assert body["sensitive"] == {"id_number": _ID_NUMBER, "health_note": "對花生過敏"}
    assert (body["has_id_number"], body["has_health_note"]) == (True, True)
    assert body["photo_url"] == (
        f"https://storage.test/student-photos/{student.id}/{'ab' * 16}.jpg?exp=300"
    )
    assert [g["name"] for g in body["guardians"]] == ["王媽媽"]
    assert body["class"] is None
    assert "class_" not in body
    assert set(body) == {
        "id",
        "student_no",
        "name",
        "gender",
        "grade_level",
        "school",
        "school_class",
        "class",
        "status",
        "archived_at",
        "birthday",
        "enrolled_on",
        "withdrawn_on",
        "note",
        "photo_url",
        "has_id_number",
        "has_health_note",
        "sensitive",
        "guardians",
    }


def test_admin_students_get_hidden(staff_client: StaffClientFactory, db_session: Session) -> None:
    student = _with_sensitive(db_session)
    archived = make_student(db_session, archived=True)
    db_session.commit()
    client, _ = staff_client(permissions=["students:read"])

    resp = client.get(f"{_URL}/{student.id}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["sensitive"] is None
    assert (body["has_id_number"], body["has_health_note"]) == (True, True)
    for leaked in (_ID_NUMBER, "對花生過敏", "_enc", "hmac"):
        assert leaked not in resp.text
    # 封存學生也可查
    archived_resp = client.get(f"{_URL}/{archived.id}")
    assert archived_resp.status_code == 200
    assert archived_resp.json()["archived_at"] is not None


def test_admin_students_get_422_401(
    staff_client: StaffClientFactory, api_client: TestClient, assert_error: AssertError
) -> None:
    client, _ = staff_client(permissions=["students:read"])

    assert_error(client.get(f"{_URL}/abc"), 422, "validation_error")
    assert_error(api_client.get(f"{_URL}/{uuid4()}"), 401, "unauthenticated")


def test_admin_students_get_403(
    staff_client: StaffClientFactory, assert_error: AssertError, db_session: Session
) -> None:
    student = make_student(db_session)
    client, _ = staff_client(permissions=["classes:read"])

    resp = client.get(f"{_URL}/{student.id}")

    assert_error(resp, 403, "permission_denied")
    assert resp.json()["error"]["details"] == {"required": ["students:read"]}


def test_admin_students_get_404(
    staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    client, _ = staff_client(permissions=["students:read", "students:sensitive"])

    assert_error(client.get(f"{_URL}/{uuid4()}"), 404, "student_not_found")


def test_admin_students_get_guard_registered(app: FastAPI) -> None:
    assert admin_routes_without_permission(app) == []
    assert "get" in app.openapi()["paths"]["/api/admin/students/{student_id}"]
