"""BACKEND-355：GET /api/parent/children/{student_id}/leaves。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.account import StaffUser
from app.models.parents import ParentAccount
from tests.support.factories import make_guardian, make_leave, make_student

ParentClientFactory = Callable[..., tuple[TestClient, ParentAccount]]
StaffClientFactory = Callable[..., tuple[TestClient, StaffUser]]
AssertError = Callable[..., None]


def _url(student_id: object) -> str:
    return f"/api/parent/children/{student_id}/leaves"


def test_parent_leaves_list_success(
    parent_client: ParentClientFactory, db_session: Session
) -> None:
    client, parent = parent_client()
    ming = make_student(db_session, name="王小明")
    make_guardian(db_session, ming, parent=parent)
    make_leave(db_session, ming, start_date=date(2026, 8, 20), reason="感冒")
    make_leave(db_session, ming, start_date=date(2026, 9, 10), leave_type="personal")
    other = make_student(db_session, name="陳小華")
    make_leave(db_session, other, start_date=date(2026, 9, 10))
    db_session.commit()

    resp = client.get(_url(ming.id))

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert len(body["items"]) == 2
    first = body["items"][0]
    assert first["start_date"] == "2026-09-10"
    assert isinstance(first["can_cancel"], bool)
    assert first["can_cancel"] is True
    assert body["items"][1]["can_cancel"] is False
    assert first["leave_type_label"] == "事假"
    assert first["attachments"] == []
    assert {i["student_id"] for i in body["items"]} == {str(ming.id)}
    # 家長端不回員工姓名欄位
    assert "created_by_name" not in first
    assert "cancelled_by_name" not in first
    paged = client.get(_url(ming.id), params={"page": 2, "page_size": 1}).json()
    assert paged["total"] == 2
    assert [i["start_date"] for i in paged["items"]] == ["2026-08-20"]


def test_parent_leaves_list_422(
    parent_client: ParentClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    client, parent = parent_client()
    ming = make_student(db_session)
    make_guardian(db_session, ming, parent=parent)
    db_session.commit()

    assert_error(client.get(_url(ming.id), params={"page_size": 500}), 422, "validation_error")
    assert_error(client.get(_url(ming.id), params={"page": 0}), 422, "validation_error")
    assert_error(client.get(_url("abc")), 422, "validation_error")


def test_parent_leaves_list_401(
    api_client: TestClient, staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    assert_error(api_client.get(_url(uuid4())), 401, "unauthenticated")
    staff, _ = staff_client(permissions=["leaves:read"])
    assert_error(staff.get(_url(uuid4())), 401, "unauthenticated")


def test_parent_leaves_list_idor(
    parent_client: ParentClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    client_a, _ = parent_client()
    _, parent_b = parent_client()
    hua = make_student(db_session, name="陳小華")
    make_guardian(db_session, hua, parent=parent_b)
    make_leave(db_session, hua, start_date=date(2026, 9, 10), reason="B 的請假")
    db_session.commit()

    theirs = client_a.get(_url(hua.id))
    missing = client_a.get(_url(uuid4()))

    assert_error(theirs, 404, "student_not_found")
    assert theirs.json() == missing.json()
    assert "B 的請假" not in theirs.text


def test_parent_leaves_list_archived_child(
    parent_client: ParentClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    client, parent = parent_client()
    ming = make_student(db_session)
    make_guardian(db_session, ming, parent=parent)
    make_leave(db_session, ming, start_date=date(2026, 9, 10))
    db_session.commit()
    assert client.get(_url(ming.id)).status_code == 200

    ming.archived_at = datetime(2026, 9, 1, tzinfo=UTC)
    db_session.commit()

    assert_error(client.get(_url(ming.id)), 404, "student_not_found")
