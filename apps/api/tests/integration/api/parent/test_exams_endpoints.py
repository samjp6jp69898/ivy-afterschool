"""BACKEND-479：GET /api/parent/children/{student_id}/exams（只含已發布且該生有成績的考試）。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.account import StaffUser
from app.models.exams import Exam
from app.models.parents import ParentAccount
from app.models.reference import Subject
from tests.support.factories import (
    make_exam,
    make_exam_score,
    make_exam_subject,
    make_guardian,
    make_student,
)

ParentClientFactory = Callable[..., tuple[TestClient, ParentAccount]]
StaffClientFactory = Callable[..., tuple[TestClient, StaffUser]]
AssertError = Callable[..., None]


def _url(student_id: object) -> str:
    return f"/api/parent/children/{student_id}/exams"


def _exam(db: Session, *, name: str, status: str, exam_date: date) -> tuple[Exam, Subject]:
    chinese = db.execute(select(Subject).where(Subject.name == "國語")).scalar_one()
    exam = make_exam(db, name=name, exam_date=exam_date, status=status)  # type: ignore[arg-type]
    make_exam_subject(db, exam, chinese)
    return exam, chinese


def _seed(db: Session, parent: ParentAccount) -> UUID:
    ming = make_student(db, name="王小明")
    make_guardian(db, ming, parent=parent)
    published, chinese = _exam(
        db, name="第一次段考", status="published", exam_date=date(2026, 10, 15)
    )
    make_exam_score(db, published, ming, chinese, score=Decimal("88"))
    draft, chinese2 = _exam(db, name="草稿小考", status="draft", exam_date=date(2026, 10, 20))
    make_exam_score(db, draft, ming, chinese2, score=Decimal("95"))
    # 已發布但該生沒有成績的考試不出現
    _exam(db, name="別班段考", status="published", exam_date=date(2026, 10, 16))
    db.commit()
    return ming.id


def test_parent_exams_list_success(parent_client: ParentClientFactory, db_session: Session) -> None:
    client, parent = parent_client()
    student_id = _seed(db_session, parent)

    resp = client.get(_url(student_id))

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["name"] == "第一次段考"
    assert item["exam_type_name"] == "段考"
    assert item["exam_date"] == "2026-10-15"
    assert item["subject_count"] == 1
    assert item["published_at"] is not None
    assert set(item) == {
        "exam_id",
        "name",
        "exam_type_name",
        "exam_date",
        "published_at",
        "subject_count",
    }


def test_parent_exams_list_422(
    parent_client: ParentClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    client, parent = parent_client()
    student_id = _seed(db_session, parent)

    assert_error(client.get(_url(student_id), params={"page": 0}), 422, "validation_error")
    assert_error(client.get(_url(student_id), params={"page_size": 201}), 422, "validation_error")
    assert_error(client.get(_url("abc")), 422, "validation_error")


def test_parent_exams_list_401(
    api_client: TestClient, staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    assert_error(api_client.get(_url(uuid4())), 401, "unauthenticated")
    staff, _ = staff_client(permissions=["exams:read"])
    assert_error(staff.get(_url(uuid4())), 401, "unauthenticated")


def test_parent_exams_list_idor(
    parent_client: ParentClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    client_a, _ = parent_client()
    _, parent_b = parent_client()
    hua_id = _seed(db_session, parent_b)

    theirs = client_a.get(_url(hua_id))
    missing = client_a.get(_url(uuid4()))

    assert_error(theirs, 404, "student_not_found")
    assert theirs.json() == missing.json()
    assert "第一次段考" not in theirs.text


def test_parent_exams_list_hides_draft(
    parent_client: ParentClientFactory, db_session: Session
) -> None:
    client, parent = parent_client()
    student_id = _seed(db_session, parent)

    body = client.get(_url(student_id)).json()

    assert [i["name"] for i in body["items"]] == ["第一次段考"]
    assert "草稿小考" not in str(body)
    assert "別班段考" not in str(body)
