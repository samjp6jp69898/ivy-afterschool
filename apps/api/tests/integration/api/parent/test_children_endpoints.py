"""BACKEND-184：GET /api/parent/children。
BACKEND-185：GET /api/parent/children/{student_id}（自己的小孩詳情；他人 / 不存在 / 封存皆同一
404）。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.account import StaffUser
from app.models.parents import ParentAccount
from tests.support.factories import make_class, make_guardian, make_school, make_student
from tests.support.fake_storage import FakeStorage

_URL = "/api/parent/children"
ParentClientFactory = Callable[..., tuple[TestClient, ParentAccount]]
StaffClientFactory = Callable[..., tuple[TestClient, StaffUser]]
AssertError = Callable[..., None]


def test_parent_children_list_success(
    parent_client: ParentClientFactory, db_session: Session, fake_storage: FakeStorage
) -> None:
    client, parent = parent_client()
    school = make_school(db_session, name="新生國民小學")
    school.short_name = "新生"
    klass = make_class(db_session, name="低年級 A 班")
    ming = make_student(db_session, name="王小明", grade_level=2, class_=klass, school=school)
    ming.photo_path = f"{ming.id}/{uuid4().hex}.jpg"
    make_guardian(db_session, ming, parent=parent)
    db_session.commit()

    resp = client.get(_URL)

    assert resp.status_code == 200
    body = resp.json()
    assert [c["name"] for c in body] == ["王小明"]
    child = body[0]
    assert child["id"] == str(ming.id)
    assert child["class_name"] == "低年級 A 班"
    assert child["school_name"] == "新生"
    assert child["photo_url"] == f"https://storage.test/student-photos/{ming.photo_path}?exp=300"
    assert child["status"] == "active"


def test_parent_children_list_401(
    api_client: TestClient, staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    assert_error(api_client.get(_URL), 401, "unauthenticated")
    staff, _ = staff_client(permissions=["students:read"])
    assert_error(staff.get(_URL), 401, "unauthenticated")


def test_parent_children_list_idor_isolation(
    parent_client: ParentClientFactory, db_session: Session
) -> None:
    client_a, parent_a = parent_client()
    _, parent_b = parent_client()
    ming = make_student(db_session, name="王小明")
    hua = make_student(db_session, name="陳小華")
    revoked = make_student(db_session, name="已解除綁定")
    make_guardian(db_session, ming, parent=parent_a)
    make_guardian(db_session, hua, parent=parent_b)
    make_guardian(db_session, revoked, parent=parent_a, archived=True)
    db_session.commit()

    resp = client_a.get(_URL)

    assert [c["name"] for c in resp.json()] == ["王小明"]
    assert "陳小華" not in resp.text
    assert "已解除綁定" not in resp.text


def test_parent_children_list_no_sensitive(
    parent_client: ParentClientFactory, db_session: Session
) -> None:
    client, parent = parent_client()
    ming = make_student(db_session, name="王小明")
    ming.id_number_enc = b"\x01encrypted-id"
    ming.id_number_hmac = "a" * 64
    ming.health_note_enc = b"\x01encrypted-health"
    ming.note = "內部備註"
    make_guardian(db_session, ming, parent=parent)
    db_session.commit()

    resp = client.get(_URL)

    assert resp.status_code == 200
    for leaked in ("A123456789", "health_note", "id_number", "encrypted", "hmac", "內部備註"):
        assert leaked not in resp.text


def test_parent_children_list_disabled_parent(
    parent_client: ParentClientFactory, assert_error: AssertError
) -> None:
    client, _ = parent_client(status="disabled")

    assert_error(client.get(_URL), 401, "unauthenticated")


def test_parent_children_list_empty(parent_client: ParentClientFactory) -> None:
    client, _ = parent_client()

    resp = client.get(_URL)

    assert resp.status_code == 200
    assert resp.json() == []


# --- BACKEND-185：GET /api/parent/children/{student_id} -------------------------------------------


def test_parent_child_get_success(parent_client: ParentClientFactory, db_session: Session) -> None:
    client, parent = parent_client()
    klass = make_class(db_session, name="低年級 A 班")
    ming = make_student(db_session, name="王小明", grade_level=2, class_=klass)
    ming.school_class = "二年三班"
    make_guardian(db_session, ming, parent=parent, relation="mother", is_primary=True)
    db_session.commit()

    resp = client.get(f"{_URL}/{ming.id}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == str(ming.id)
    assert body["name"] == "王小明"
    assert body["class_name"] == "低年級 A 班"
    assert body["school_class"] == "二年三班"
    assert body["my_guardian"]["relation"] == "mother"
    assert body["my_guardian"]["is_primary"] is True
    assert set(body) == {
        "id",
        "name",
        "grade_level",
        "class_name",
        "school_name",
        "photo_url",
        "status",
        "school_class",
        "enrolled_on",
        "my_guardian",
    }


def test_parent_child_get_idor(
    parent_client: ParentClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    client_a, _ = parent_client()
    _, parent_b = parent_client()
    hua = make_student(db_session, name="陳小華")
    make_guardian(db_session, hua, parent=parent_b)
    db_session.commit()

    theirs = client_a.get(f"{_URL}/{hua.id}")
    missing = client_a.get(f"{_URL}/{uuid4()}")

    assert_error(theirs, 404, "student_not_found")
    assert_error(missing, 404, "student_not_found")
    assert theirs.json() == missing.json()
    assert "陳小華" not in theirs.text


def test_parent_child_get_422(
    parent_client: ParentClientFactory, assert_error: AssertError
) -> None:
    client, _ = parent_client()

    assert_error(client.get(f"{_URL}/abc"), 422, "validation_error")


def test_parent_child_get_401(
    api_client: TestClient, staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    assert_error(api_client.get(f"{_URL}/{uuid4()}"), 401, "unauthenticated")
    staff, _ = staff_client(permissions=["students:read"])
    assert_error(staff.get(f"{_URL}/{uuid4()}"), 401, "unauthenticated")


def test_parent_child_get_archived(
    parent_client: ParentClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    client, parent = parent_client()
    ming = make_student(db_session, name="王小明")
    make_guardian(db_session, ming, parent=parent)
    db_session.commit()
    assert client.get(f"{_URL}/{ming.id}").status_code == 200

    ming.archived_at = datetime(2026, 9, 1, tzinfo=UTC)
    db_session.commit()

    assert_error(client.get(f"{_URL}/{ming.id}"), 404, "student_not_found")
