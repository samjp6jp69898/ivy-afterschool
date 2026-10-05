"""BACKEND-323：GET /api/parent/children/{student_id}/attendance?month=YYYY-MM。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.account import StaffUser
from app.models.parents import ParentAccount
from tests.support.factories import make_attendance, make_guardian, make_student

ParentClientFactory = Callable[..., tuple[TestClient, ParentAccount]]
StaffClientFactory = Callable[..., tuple[TestClient, StaffUser]]
AssertError = Callable[..., None]


def _url(student_id: object) -> str:
    return f"/api/parent/children/{student_id}/attendance"


def _seed(db: Session, parent: ParentAccount) -> object:
    ming = make_student(db, name="王小明")
    make_guardian(db, ming, parent=parent)
    make_attendance(db, ming, service_date=date(2026, 9, 1), status="left", note="內部備註")
    make_attendance(db, ming, service_date=date(2026, 9, 2), status="absent")
    db.commit()
    return ming.id


def test_parent_attendance_success(parent_client: ParentClientFactory, db_session: Session) -> None:
    client, parent = parent_client()
    student_id = _seed(db_session, parent)

    resp = client.get(_url(student_id), params={"month": "2026-09"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["student_id"] == str(student_id)
    assert body["month"] == "2026-09"
    assert len(body["days"]) == 30
    assert body["days"][0]["date"] == "2026-09-01"
    assert body["days"][0]["status"] == "left"
    assert body["days"][0]["check_out_at"] is not None
    assert body["days"][1]["status"] == "absent"
    assert set(body["days"][0]) == {
        "date",
        "is_service_day",
        "status",
        "check_in_at",
        "check_out_at",
        "leave_type",
    }
    assert set(body["stats"]) == {"service_days", "attended", "absent", "leave", "unrecorded"}
    assert "內部備註" not in resp.text
    assert "note" not in body["days"][0]


@pytest.mark.clock("2026-09-15T10:00:00+08:00")
def test_parent_attendance_default_month(
    parent_client: ParentClientFactory, db_session: Session
) -> None:
    client, parent = parent_client()
    student_id = _seed(db_session, parent)

    resp = client.get(_url(student_id))

    assert resp.status_code == 200
    assert resp.json()["month"] == "2026-09"
    assert resp.json()["days"][0]["status"] == "left"
    august = client.get(_url(student_id), params={"month": "2026-08"}).json()
    assert august["month"] == "2026-08"
    assert len(august["days"]) == 31


def test_parent_attendance_422(
    parent_client: ParentClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    client, parent = parent_client()
    student_id = _seed(db_session, parent)

    for month in ("2026-13", "2026/09", "202609", ""):
        assert_error(client.get(_url(student_id), params={"month": month}), 422, "validation_error")
    assert_error(client.get(_url("abc"), params={"month": "2026-09"}), 422, "validation_error")
    assert_error(client.get(_url(student_id), params={"foo": "bar"}), 422, "validation_error")


def test_parent_attendance_401(
    api_client: TestClient, staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    assert_error(api_client.get(_url(uuid4()), params={"month": "2026-09"}), 401, "unauthenticated")
    staff, _ = staff_client(permissions=["attendance:read"])
    assert_error(staff.get(_url(uuid4()), params={"month": "2026-09"}), 401, "unauthenticated")


def test_parent_attendance_idor(
    parent_client: ParentClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    client_a, _ = parent_client()
    _, parent_b = parent_client()
    hua_id = _seed(db_session, parent_b)

    theirs = client_a.get(_url(hua_id), params={"month": "2026-09"})
    missing = client_a.get(_url(uuid4()), params={"month": "2026-09"})

    assert_error(theirs, 404, "student_not_found")
    assert theirs.json() == missing.json()
    assert "left" not in theirs.text
