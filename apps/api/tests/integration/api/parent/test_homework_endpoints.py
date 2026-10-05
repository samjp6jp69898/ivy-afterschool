"""BACKEND-391：GET /api/parent/children/{student_id}/homework。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, time
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.account import StaffUser
from app.models.parents import ParentAccount
from app.models.reference import Subject
from tests.support.factories import (
    make_guardian,
    make_homework_item,
    make_homework_progress,
    make_student,
)
from tests.support.fake_clock import FakeClock

ParentClientFactory = Callable[..., tuple[TestClient, ParentAccount]]
StaffClientFactory = Callable[..., tuple[TestClient, StaffUser]]
AssertError = Callable[..., None]
_DAY = date(2026, 9, 1)  # fake_clock 預設台北日期


def _url(student_id: object) -> str:
    return f"/api/parent/children/{student_id}/homework"


def _seed(db: Session, parent: ParentAccount) -> object:
    ming = make_student(db, name="王小明")
    make_guardian(db, ming, parent=parent)
    math = db.execute(select(Subject).where(Subject.name == "數學")).scalar_one()
    make_homework_item(
        db, ming, service_date=_DAY, title="數學習作 p.12", subject=math, status="doing"
    )
    make_homework_item(db, ming, service_date=_DAY, title="國語生字", status="done", sort_order=1)
    make_homework_item(db, ming, service_date=date(2026, 9, 2), title="隔天")
    make_homework_progress(
        db, ming, service_date=_DAY, overall_status="in_progress", ready_eta=time(17, 30)
    )
    db.commit()
    return ming.id


def test_parent_homework_success(parent_client: ParentClientFactory, db_session: Session) -> None:
    client, parent = parent_client()
    student_id = _seed(db_session, parent)

    resp = client.get(_url(student_id), params={"date": "2026-09-01"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["student_id"] == str(student_id)
    assert body["date"] == "2026-09-01"
    assert body["overall_status"] == "in_progress"
    assert body["ready_eta"] == "17:30"
    assert [(i["title"], i["subject_name"], i["status"]) for i in body["items"]] == [
        ("數學習作 p.12", "數學", "doing"),
        ("國語生字", None, "done"),
    ]
    assert set(body) == {
        "student_id",
        "date",
        "items",
        "overall_status",
        "ready_eta",
        "note",
        "updated_at",
    }


def test_parent_homework_default_date(
    parent_client: ParentClientFactory, db_session: Session, fake_clock: FakeClock
) -> None:
    client, parent = parent_client()
    student_id = _seed(db_session, parent)

    resp = client.get(_url(student_id))

    assert resp.status_code == 200
    assert resp.json()["date"] == fake_clock.today().isoformat() == "2026-09-01"
    assert len(resp.json()["items"]) == 2
    tomorrow = client.get(_url(student_id), params={"date": "2026-09-02"}).json()
    assert [i["title"] for i in tomorrow["items"]] == ["隔天"]
    assert tomorrow["overall_status"] == "not_started"


def test_parent_homework_422(
    parent_client: ParentClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    client, parent = parent_client()
    student_id = _seed(db_session, parent)

    assert_error(
        client.get(_url(student_id), params={"date": "2026-02-30"}), 422, "validation_error"
    )
    assert_error(client.get(_url("abc")), 422, "validation_error")


def test_parent_homework_401(
    api_client: TestClient, staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    assert_error(api_client.get(_url(uuid4())), 401, "unauthenticated")
    staff, _ = staff_client(permissions=["homework:read"])
    assert_error(staff.get(_url(uuid4())), 401, "unauthenticated")


def test_parent_homework_idor(
    parent_client: ParentClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    client_a, _ = parent_client()
    _, parent_b = parent_client()
    hua_id = _seed(db_session, parent_b)

    theirs = client_a.get(_url(hua_id))
    missing = client_a.get(_url(uuid4()))

    assert_error(theirs, 404, "student_not_found")
    assert theirs.json() == missing.json()
    assert "數學習作" not in theirs.text
