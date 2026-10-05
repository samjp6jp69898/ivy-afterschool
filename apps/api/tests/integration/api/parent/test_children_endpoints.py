"""BACKEND-184：GET /api/parent/children。
BACKEND-185：GET /api/parent/children/{student_id}（自己的小孩詳情；他人 / 不存在 / 封存皆同一
404）。
BACKEND-526：GET /api/parent/children/{student_id}/today（今日狀態卡：出勤 / 請假 / 作業 /
接送）。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, time
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.clock import combine_taipei
from app.models.account import StaffUser
from app.models.parents import ParentAccount
from tests.support.factories import (
    make_attendance,
    make_class,
    make_guardian,
    make_homework_item,
    make_homework_progress,
    make_pickup_request,
    make_school,
    make_student,
)
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


# --- BACKEND-185：GET /api/parent/children/{student_id} ----------------------------


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


# --- BACKEND-526：GET /api/parent/children/{student_id}/today ----------------------

_DAY = date(2026, 9, 1)  # fake_clock 預設台北日期


def _today_url(student_id: object) -> str:
    return f"{_URL}/{student_id}/today"


def _seed_today(db: Session, parent: ParentAccount) -> object:
    ming = make_student(db, name="王小明")
    make_guardian(db, ming, parent=parent)
    make_attendance(db, ming, service_date=_DAY, status="present", note="內部備註")
    make_homework_item(db, ming, service_date=_DAY, status="done")
    make_homework_item(db, ming, service_date=_DAY, status="done", title="國語生字")
    make_homework_item(db, ming, service_date=_DAY, status="doing", title="英語")
    make_homework_progress(
        db, ming, service_date=_DAY, overall_status="in_progress", ready_eta=time(17, 30)
    )
    make_pickup_request(
        db,
        ming,
        service_date=_DAY,
        reply_source="auto",
        reply_message="預計 17:30 可接送",
        reply_ready_eta=time(17, 30),
        expected_arrival_at=combine_taipei(_DAY, time(17, 45)),
    )
    db.commit()
    return ming.id


def test_parent_child_today_success(
    parent_client: ParentClientFactory, db_session: Session
) -> None:
    client, parent = parent_client()
    student_id = _seed_today(db_session, parent)

    resp = client.get(_today_url(student_id))

    assert resp.status_code == 200
    body = resp.json()
    assert body["student_id"] == str(student_id)
    assert body["date"] == "2026-09-01"
    assert body["is_service_day"] is True
    assert body["attendance"]["status"] == "present"
    assert body["attendance"]["check_in_at"] is not None
    assert (body["homework"]["item_count"], body["homework"]["done_count"]) == (3, 2)
    assert body["homework"]["overall_status"] == "in_progress"
    assert body["homework"]["ready_eta"] == "17:30"
    assert body["pickup_request"]["reply_source"] == "auto"
    assert body["pickup_request"]["status"] == "pending"
    assert body["pickup_request"]["expected_arrival_at"] == "17:45"
    assert (body["on_leave"], body["leave"]) == (False, None)
    assert "內部備註" not in resp.text
    assert set(body) == {
        "student_id",
        "date",
        "is_service_day",
        "attendance",
        "on_leave",
        "leave",
        "homework",
        "pickup_request",
    }


def test_parent_child_today_422(
    parent_client: ParentClientFactory, assert_error: AssertError
) -> None:
    client, _ = parent_client()

    assert_error(client.get(_today_url("abc")), 422, "validation_error")


def test_parent_child_today_401(
    api_client: TestClient, staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    assert_error(api_client.get(_today_url(uuid4())), 401, "unauthenticated")
    staff, _ = staff_client(permissions=["students:read"])
    assert_error(staff.get(_today_url(uuid4())), 401, "unauthenticated")


def test_parent_child_today_idor(
    parent_client: ParentClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    client_a, _ = parent_client()
    _, parent_b = parent_client()
    hua_id = _seed_today(db_session, parent_b)

    theirs = client_a.get(_today_url(hua_id))
    missing = client_a.get(_today_url(uuid4()))

    assert_error(theirs, 404, "student_not_found")
    assert theirs.json() == missing.json()
    assert "國語生字" not in theirs.text


def test_parent_child_today_archived(
    parent_client: ParentClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    client, parent = parent_client()
    ming = make_student(db_session, name="王小明")
    make_guardian(db_session, ming, parent=parent)
    db_session.commit()
    assert client.get(_today_url(ming.id)).status_code == 200

    ming.archived_at = datetime(2026, 9, 1, tzinfo=UTC)
    db_session.commit()

    assert_error(client.get(_today_url(ming.id)), 404, "student_not_found")
