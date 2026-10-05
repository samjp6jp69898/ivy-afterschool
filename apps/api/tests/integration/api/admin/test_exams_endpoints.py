"""BACKEND-467 / 468 / 469 / 471 / 472 / 473 / 476 / 477 / 478：後台考試成績 endpoint
（app/api/admin/exams.py）。
BACKEND-470 / 474 / 475：PATCH /{id}、PUT /{id}/scores、POST /{id}/publish。

測資以 db_session 建立並 commit（只釋放 savepoint）後再打 API；科目 / 考試類型用 seed 列。
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.account import StaffUser
from app.models.audit import AuditLog
from app.models.exams import Exam, ExamScore
from app.models.notifications import Notification
from app.models.reference import ExamType, Subject
from app.notifications import outbox_jobs
from tests.support.factories import (
    make_class,
    make_exam,
    make_exam_score,
    make_exam_subject,
    make_guardian,
    make_parent,
    make_student,
)
from tests.support.route_audit import admin_routes_without_permission

_URL = "/api/admin/exams"
StaffClientFactory = Callable[..., tuple[TestClient, StaffUser]]
AssertError = Callable[..., None]


def _subject(db: Session, name: str) -> Subject:
    return db.execute(select(Subject).where(Subject.name == name)).scalar_one()


def _exam_type(db: Session, name: str = "段考") -> ExamType:
    return db.execute(select(ExamType).where(ExamType.name == name)).scalar_one()


def _exam_with_subjects(db: Session, *names: str, **kwargs: Any) -> Exam:
    exam = make_exam(db, **kwargs)
    for index, name in enumerate(names, start=1):
        make_exam_subject(db, exam, _subject(db, name), sort_order=index * 10)
    return exam


def _exam_url(exam_id: object, suffix: str = "") -> str:
    return f"{_URL}/{exam_id}{suffix}"


def test_admin_exams_guard_registered(app: FastAPI) -> None:
    assert admin_routes_without_permission(app) == []
    paths = app.openapi()["paths"]
    assert {"get", "post"} <= set(paths[_URL])
    assert {"get", "delete"} <= set(paths[_URL + "/{exam_id}"])
    assert "put" in paths[_URL + "/{exam_id}/subjects"]
    assert "get" in paths[_URL + "/{exam_id}/scores"]
    assert "post" in paths[_URL + "/{exam_id}/unpublish"]
    assert "get" in paths[_URL + "/{exam_id}/summary"]
    assert "get" in paths["/api/admin/students/{student_id}/exam-history"]


# --- BACKEND-467：GET /api/admin/exams ---------------------------------------------------------


def test_admin_exams_list_success(staff_client: StaffClientFactory, db_session: Session) -> None:
    make_exam(db_session, name="列表測試-第一次段考", exam_date=date(2026, 10, 15))
    make_exam(db_session, name="列表測試-第二次段考", exam_date=date(2026, 11, 20))
    client, _ = staff_client(permissions=["exams:read"])

    resp = client.get(_URL, params={"q": "列表測試"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert [e["name"] for e in body["items"]] == ["列表測試-第二次段考", "列表測試-第一次段考"]
    first = body["items"][0]
    assert first["status"] == "draft"
    assert first["exam_type"]["name"] == "段考"
    assert first["subjects"] == []
    assert set(first) == {
        "id",
        "name",
        "exam_type",
        "exam_date",
        "grade_level",
        "class",
        "status",
        "published_at",
        "published_by_name",
        "note",
        "subjects",
        "roster_count",
        "created_at",
    }
    # 分頁
    page = client.get(_URL, params={"q": "列表測試", "page_size": 1, "page": 2}).json()
    assert (page["total"], len(page["items"])) == (2, 1)


def test_admin_exams_list_422(staff_client: StaffClientFactory, assert_error: AssertError) -> None:
    client, _ = staff_client(permissions=["exams:read"])

    assert_error(client.get(_URL, params={"status": "archived"}), 422, "validation_error")
    assert_error(client.get(_URL, params={"grade_level": 7}), 422, "validation_error")
    assert_error(client.get(_URL, params={"foo": "bar"}), 422, "validation_error")
    assert_error(client.get(_URL, params={"page_size": 0}), 422, "validation_error")


def test_admin_exams_list_401(api_client: TestClient, assert_error: AssertError) -> None:
    assert_error(api_client.get(_URL), 401, "unauthenticated")


def test_admin_exams_list_403(staff_client: StaffClientFactory, assert_error: AssertError) -> None:
    client, _ = staff_client(permissions=["students:read"])

    resp = client.get(_URL)

    assert_error(resp, 403, "permission_denied")
    assert resp.json()["error"]["details"] == {"required": ["exams:read"]}


def test_admin_exams_list_filter_empty(
    staff_client: StaffClientFactory, db_session: Session
) -> None:
    make_exam(db_session, name="篩選測試")
    client, _ = staff_client(permissions=["exams:read"])

    resp = client.get(_URL, params={"exam_type_id": str(uuid4())})

    assert resp.status_code == 200
    assert resp.json() == {"items": [], "total": 0}


# --- BACKEND-468：POST /api/admin/exams --------------------------------------------------------


def _create_body(db: Session, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "第一次段考",
        "exam_type_id": str(_exam_type(db).id),
        "exam_date": "2026-10-15",
        "grade_level": 3,
    }
    body.update(overrides)
    return body


def test_admin_exams_create_success(staff_client: StaffClientFactory, db_session: Session) -> None:
    make_student(db_session, grade_level=3)
    make_student(db_session, grade_level=4)
    client, _ = staff_client(permissions=["exams:write"])

    resp = client.post(_URL, json=_create_body(db_session, note="第一次"))

    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "draft"
    assert body["name"] == "第一次段考"
    assert body["grade_level"] == 3
    assert body["class"] is None
    assert body["published_at"] is None
    assert body["subjects"] == []
    assert body["roster_count"] == 1
    assert body["note"] == "第一次"
    # 已 commit
    assert db_session.execute(select(Exam).where(Exam.id == UUID(body["id"]))).scalar_one()


def test_admin_exams_create_422(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    inactive = ExamType(name="停用類型", sort_order=99, is_active=False)
    db_session.add(inactive)
    db_session.flush()
    client, _ = staff_client(permissions=["exams:write"])

    no_scope = client.post(_URL, json=_create_body(db_session, grade_level=None))
    extra = client.post(_URL, json=_create_body(db_session, status="published"))
    bad_type = client.post(_URL, json=_create_body(db_session, exam_type_id=str(inactive.id)))

    assert_error(no_scope, 422, "validation_error")
    assert_error(extra, 422, "validation_error")
    assert_error(bad_type, 422, "invalid_exam_type")
    assert db_session.execute(select(Exam).where(Exam.name == "第一次段考")).first() is None


def test_admin_exams_create_401(
    api_client: TestClient, db_session: Session, assert_error: AssertError
) -> None:
    assert_error(api_client.post(_URL, json=_create_body(db_session)), 401, "unauthenticated")


def test_admin_exams_create_403(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    client, _ = staff_client(permissions=["exams:read"])

    resp = client.post(_URL, json=_create_body(db_session))

    assert_error(resp, 403, "permission_denied")
    assert resp.json()["error"]["details"] == {"required": ["exams:write"]}


def test_admin_exams_create_business(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    class_a = make_class(db_session, grade_levels=(3,))
    client, _ = staff_client(permissions=["exams:write"])

    resp = client.post(_URL, json=_create_body(db_session, class_id=str(class_a.id), grade_level=4))

    assert_error(resp, 422, "grade_not_in_class")
    assert resp.json()["error"]["details"] == {"grade_levels": [3]}
    ok = client.post(_URL, json=_create_body(db_session, class_id=str(class_a.id), grade_level=3))
    assert ok.status_code == 201
    assert ok.json()["class"]["id"] == str(class_a.id)


# --- BACKEND-469：GET /api/admin/exams/{id} ----------------------------------------------------


def test_admin_exams_get_success(staff_client: StaffClientFactory, db_session: Session) -> None:
    exam = _exam_with_subjects(db_session, "國語", "數學")
    client, _ = staff_client(permissions=["exams:read"])

    resp = client.get(_exam_url(exam.id))

    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == str(exam.id)
    assert [s["subject_name"] for s in body["subjects"]] == ["國語", "數學"]
    assert body["subjects"][0]["full_score"] == 100.0
    assert set(body["subjects"][0]) == {"subject_id", "subject_name", "full_score", "sort_order"}


def test_admin_exams_get_422(staff_client: StaffClientFactory, assert_error: AssertError) -> None:
    client, _ = staff_client(permissions=["exams:read"])

    assert_error(client.get(_exam_url("abc")), 422, "validation_error")


def test_admin_exams_get_401(api_client: TestClient, assert_error: AssertError) -> None:
    assert_error(api_client.get(_exam_url(uuid4())), 401, "unauthenticated")


def test_admin_exams_get_403(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    exam = make_exam(db_session)
    client, _ = staff_client(permissions=["students:read"])

    assert_error(client.get(_exam_url(exam.id)), 403, "permission_denied")


def test_admin_exams_get_404(staff_client: StaffClientFactory, assert_error: AssertError) -> None:
    client, _ = staff_client(permissions=["exams:read"])

    assert_error(client.get(_exam_url(uuid4())), 404, "exam_not_found")


# --- BACKEND-471：DELETE /api/admin/exams/{id} -------------------------------------------------


def test_admin_exams_delete_success(staff_client: StaffClientFactory, db_session: Session) -> None:
    exam = _exam_with_subjects(db_session, "國語")
    client, _ = staff_client(permissions=["exams:write", "exams:read"])

    resp = client.delete(_exam_url(exam.id))

    assert resp.status_code == 204
    assert resp.content == b""
    assert client.get(_exam_url(exam.id)).status_code == 404
    assert db_session.execute(select(Exam).where(Exam.id == exam.id)).first() is None


def test_admin_exams_delete_422(
    staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    client, _ = staff_client(permissions=["exams:write"])

    assert_error(client.delete(_exam_url("abc")), 422, "validation_error")


def test_admin_exams_delete_401(api_client: TestClient, assert_error: AssertError) -> None:
    assert_error(api_client.delete(_exam_url(uuid4())), 401, "unauthenticated")


def test_admin_exams_delete_403(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    exam = make_exam(db_session)
    client, _ = staff_client(permissions=["exams:read"])

    assert_error(client.delete(_exam_url(exam.id)), 403, "permission_denied")
    assert db_session.execute(select(Exam).where(Exam.id == exam.id)).first() is not None


def test_admin_exams_delete_409(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    exam = make_exam(db_session, status="published")
    client, _ = staff_client(permissions=["exams:write"])

    resp = client.delete(_exam_url(exam.id))

    assert_error(resp, 409, "exam_published")
    assert_error(client.delete(_exam_url(uuid4())), 404, "exam_not_found")
    db_session.expire_all()
    assert db_session.execute(select(Exam).where(Exam.id == exam.id)).first() is not None


# --- BACKEND-472：PUT /api/admin/exams/{id}/subjects -------------------------------------------


def _subjects_body(db: Session, *names: str, full_score: float | None = None) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for index, name in enumerate(names, start=1):
        item: dict[str, Any] = {"subject_id": str(_subject(db, name).id), "sort_order": index}
        if full_score is not None:
            item["full_score"] = full_score
        items.append(item)
    return {"items": items}


def test_admin_exam_subjects_success(staff_client: StaffClientFactory, db_session: Session) -> None:
    exam = make_exam(db_session)
    client, _ = staff_client(permissions=["exams:write"])

    resp = client.put(
        _exam_url(exam.id, "/subjects"), json=_subjects_body(db_session, "國語", "數學")
    )

    assert resp.status_code == 200
    body = resp.json()
    assert [s["subject_name"] for s in body["subjects"]] == ["國語", "數學"]
    assert body["subjects"][0]["full_score"] == 100.0
    assert body["id"] == str(exam.id)
    # 再設定只剩數學：國語被移除
    again = client.put(_exam_url(exam.id, "/subjects"), json=_subjects_body(db_session, "數學"))
    assert [s["subject_name"] for s in again.json()["subjects"]] == ["數學"]


def test_admin_exam_subjects_422(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    exam = make_exam(db_session)
    inactive = Subject(name="停用科目", sort_order=99, is_active=False)
    db_session.add(inactive)
    db_session.flush()
    client, _ = staff_client(permissions=["exams:write"])
    chinese = str(_subject(db_session, "國語").id)

    duplicate = client.put(
        _exam_url(exam.id, "/subjects"),
        json={"items": [{"subject_id": chinese}, {"subject_id": chinese}]},
    )
    empty = client.put(_exam_url(exam.id, "/subjects"), json={"items": []})
    bad_subject = client.put(
        _exam_url(exam.id, "/subjects"), json={"items": [{"subject_id": str(inactive.id)}]}
    )

    assert_error(duplicate, 422, "validation_error")
    assert_error(empty, 422, "validation_error")
    assert_error(bad_subject, 422, "invalid_subject")


def test_admin_exam_subjects_401(
    api_client: TestClient, db_session: Session, assert_error: AssertError
) -> None:
    resp = api_client.put(_exam_url(uuid4(), "/subjects"), json=_subjects_body(db_session, "國語"))

    assert_error(resp, 401, "unauthenticated")


def test_admin_exam_subjects_403(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    exam = make_exam(db_session)
    client, _ = staff_client(permissions=["exams:read"])

    resp = client.put(_exam_url(exam.id, "/subjects"), json=_subjects_body(db_session, "國語"))

    assert_error(resp, 403, "permission_denied")


def test_admin_exam_subjects_409(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    class_a = make_class(db_session, grade_levels=(3,))
    exam = _exam_with_subjects(db_session, "數學", grade_level=None, class_=class_a)
    student = make_student(db_session, class_=class_a)
    make_exam_score(db_session, exam, student, _subject(db_session, "數學"), score=Decimal("95"))
    published = make_exam(db_session, status="published")
    client, _ = staff_client(permissions=["exams:write"])

    lowered = client.put(
        _exam_url(exam.id, "/subjects"), json=_subjects_body(db_session, "數學", full_score=90)
    )
    locked = client.put(
        _exam_url(published.id, "/subjects"), json=_subjects_body(db_session, "數學")
    )

    assert_error(lowered, 409, "full_score_below_existing")
    assert_error(locked, 409, "exam_published")
    assert_error(
        client.put(_exam_url(uuid4(), "/subjects"), json=_subjects_body(db_session, "數學")),
        404,
        "exam_not_found",
    )


# --- BACKEND-473：GET /api/admin/exams/{id}/scores ---------------------------------------------


def test_admin_exam_scores_get_success(
    staff_client: StaffClientFactory, db_session: Session
) -> None:
    class_a = make_class(db_session, name="A班", grade_levels=(3,))
    exam = _exam_with_subjects(db_session, "國語", "數學", grade_level=None, class_=class_a)
    ming = make_student(db_session, name="王小明", student_no="G-001", class_=class_a)
    hua = make_student(db_session, name="陳小華", student_no="G-002", class_=class_a)
    chinese, math = _subject(db_session, "國語"), _subject(db_session, "數學")
    make_exam_score(db_session, exam, ming, chinese, score=Decimal("90"))
    make_exam_score(db_session, exam, ming, math, score=Decimal("88.5"))
    make_exam_score(db_session, exam, hua, chinese, score=None, is_absent=True)
    client, _ = staff_client(permissions=["exams:read"])

    resp = client.get(_exam_url(exam.id, "/scores"))

    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"exam", "students", "subjects", "cells"}
    assert body["exam"]["id"] == str(exam.id)
    assert [s["name"] for s in body["students"]] == ["王小明", "陳小華"]
    assert all(s["in_roster"] for s in body["students"])
    assert [s["subject_name"] for s in body["subjects"]] == ["國語", "數學"]
    assert len(body["cells"]) == 3
    math_cell = next(
        c
        for c in body["cells"]
        if c["student_id"] == str(ming.id) and c["subject_id"] == str(math.id)
    )
    assert math_cell["score"] == 88.5
    absent = next(c for c in body["cells"] if c["student_id"] == str(hua.id))
    assert (absent["score"], absent["is_absent"]) == (None, True)


def test_admin_exam_scores_get_422(
    staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    client, _ = staff_client(permissions=["exams:read"])

    assert_error(client.get(_exam_url("abc", "/scores")), 422, "validation_error")


def test_admin_exam_scores_get_401(api_client: TestClient, assert_error: AssertError) -> None:
    assert_error(api_client.get(_exam_url(uuid4(), "/scores")), 401, "unauthenticated")


def test_admin_exam_scores_get_403(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    exam = make_exam(db_session)
    client, _ = staff_client(permissions=["students:read"])

    assert_error(client.get(_exam_url(exam.id, "/scores")), 403, "permission_denied")


def test_admin_exam_scores_get_404(
    staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    client, _ = staff_client(permissions=["exams:read"])

    assert_error(client.get(_exam_url(uuid4(), "/scores")), 404, "exam_not_found")


# --- BACKEND-476：POST /api/admin/exams/{id}/unpublish -----------------------------------------


def test_admin_exam_unpublish_success(
    staff_client: StaffClientFactory, db_session: Session
) -> None:
    exam = make_exam(db_session, status="published")
    client, _ = staff_client(permissions=["exams:publish"])

    resp = client.post(_exam_url(exam.id, "/unpublish"))

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "draft"
    assert body["published_at"] is None
    assert body["published_by_name"] is None
    db_session.expire_all()
    assert exam.status == "draft"


def test_admin_exam_unpublish_422(
    staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    client, _ = staff_client(permissions=["exams:publish"])

    assert_error(client.post(_exam_url("abc", "/unpublish")), 422, "validation_error")


def test_admin_exam_unpublish_401(api_client: TestClient, assert_error: AssertError) -> None:
    assert_error(api_client.post(_exam_url(uuid4(), "/unpublish")), 401, "unauthenticated")


def test_admin_exam_unpublish_403(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    exam = make_exam(db_session, status="published")
    client, _ = staff_client(permissions=["exams:write"])

    resp = client.post(_exam_url(exam.id, "/unpublish"))

    assert_error(resp, 403, "permission_denied")
    assert resp.json()["error"]["details"] == {"required": ["exams:publish"]}
    db_session.expire_all()
    assert exam.status == "published"


def test_admin_exam_unpublish_409(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    exam = make_exam(db_session)
    client, _ = staff_client(permissions=["exams:publish"])

    assert_error(client.post(_exam_url(exam.id, "/unpublish")), 409, "exam_not_published")
    assert_error(client.post(_exam_url(uuid4(), "/unpublish")), 404, "exam_not_found")


# --- BACKEND-477：GET /api/admin/exams/{id}/summary --------------------------------------------


def test_admin_exam_summary_success(staff_client: StaffClientFactory, db_session: Session) -> None:
    class_a = make_class(db_session, grade_levels=(3,))
    exam = _exam_with_subjects(db_session, "數學", "英語", grade_level=None, class_=class_a)
    math = _subject(db_session, "數學")
    students = [make_student(db_session, class_=class_a) for _ in range(4)]
    make_exam_score(db_session, exam, students[0], math, score=Decimal("90"))
    make_exam_score(db_session, exam, students[1], math, score=Decimal("80"))
    make_exam_score(db_session, exam, students[2], math, score=None, is_absent=True)
    client, _ = staff_client(permissions=["exams:read"])

    resp = client.get(_exam_url(exam.id, "/summary"))

    assert resp.status_code == 200
    body = resp.json()
    assert (body["exam_id"], body["roster_count"]) == (str(exam.id), 4)
    math_row, english_row = body["subjects"]
    assert math_row["subject_name"] == "數學"
    assert math_row["average"] == 85.0
    assert (math_row["scored_count"], math_row["absent_count"], math_row["missing_count"]) == (
        2,
        1,
        1,
    )
    assert (math_row["max"], math_row["min"]) == (90.0, 80.0)
    assert english_row["average"] is None


def test_admin_exam_summary_422(
    staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    client, _ = staff_client(permissions=["exams:read"])

    assert_error(client.get(_exam_url("abc", "/summary")), 422, "validation_error")


def test_admin_exam_summary_401(api_client: TestClient, assert_error: AssertError) -> None:
    assert_error(api_client.get(_exam_url(uuid4(), "/summary")), 401, "unauthenticated")


def test_admin_exam_summary_403(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    exam = make_exam(db_session)
    client, _ = staff_client(permissions=["students:read"])

    assert_error(client.get(_exam_url(exam.id, "/summary")), 403, "permission_denied")


def test_admin_exam_summary_404(
    staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    client, _ = staff_client(permissions=["exams:read"])

    assert_error(client.get(_exam_url(uuid4(), "/summary")), 404, "exam_not_found")


# --- BACKEND-478：GET /api/admin/students/{id}/exam-history ------------------------------------


def _history_url(student_id: object) -> str:
    return f"/api/admin/students/{student_id}/exam-history"


def test_admin_exam_history_success(staff_client: StaffClientFactory, db_session: Session) -> None:
    ming = make_student(db_session, name="王小明")
    chinese = _subject(db_session, "國語")
    quiz = _exam_with_subjects(db_session, "國語", name="小考", exam_date=date(2026, 9, 30))
    midterm = _exam_with_subjects(
        db_session, "國語", "數學", name="第一次段考", exam_date=date(2026, 10, 15)
    )
    make_exam_score(db_session, quiz, ming, chinese, score=Decimal("70"))
    make_exam_score(db_session, midterm, ming, chinese, score=Decimal("92"))
    # 別人的成績不出現
    make_exam_score(db_session, midterm, make_student(db_session, name="陳小華"), chinese)
    client, _ = staff_client(permissions=["exams:read"])

    resp = client.get(_history_url(ming.id))

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    assert body[0]["exam_name"] == "第一次段考"
    assert body[0]["exam_type_name"] == "段考"
    assert [s["subject_name"] for s in body[0]["scores"]] == ["國語", "數學"]
    assert body[0]["scores"][0]["score"] == 92.0
    assert body[0]["scores"][1]["score"] is None
    assert body[1]["exam_name"] == "小考"
    assert set(body[0]) == {
        "exam_id",
        "exam_name",
        "exam_type_name",
        "exam_date",
        "status",
        "scores",
    }


def test_admin_exam_history_422(
    staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    client, _ = staff_client(permissions=["exams:read"])

    assert_error(client.get(_history_url("abc")), 422, "validation_error")


def test_admin_exam_history_401(api_client: TestClient, assert_error: AssertError) -> None:
    assert_error(api_client.get(_history_url(uuid4())), 401, "unauthenticated")


def test_admin_exam_history_403(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    ming = make_student(db_session)
    client, _ = staff_client(permissions=["students:read"])

    assert_error(client.get(_history_url(ming.id)), 403, "permission_denied")


def test_admin_exam_history_404(
    staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    client, _ = staff_client(permissions=["exams:read"])

    assert_error(client.get(_history_url(uuid4())), 404, "student_not_found")


# --- BACKEND-470 / 474 / 475：PATCH /{id}、PUT /{id}/scores、POST /{id}/publish ------------------


@pytest.fixture(autouse=True)
def _kick_off() -> Iterator[None]:
    """publish / 發布後改分會 enqueue 家長通知：commit 後的 outbox kick 不實際派送。"""
    outbox_jobs.set_kick_mode("off")
    yield
    outbox_jobs.set_kick_mode("thread")


def test_admin_exams_update_success(staff_client: StaffClientFactory, db_session: Session) -> None:
    exam = make_exam(db_session, name="第一次段考")
    client, _ = staff_client(permissions=["exams:write"])

    resp = client.patch(_exam_url(exam.id), json={"name": "第一次段考（補考）"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "第一次段考（補考）"
    assert body["id"] == str(exam.id)
    db_session.expire_all()
    assert exam.name == "第一次段考（補考）"
    moved = client.patch(_exam_url(exam.id), json={"exam_date": "2026-10-20", "note": "延後"})
    assert (moved.json()["exam_date"], moved.json()["note"]) == ("2026-10-20", "延後")


def test_admin_exams_update_422(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    exam = make_exam(db_session)
    client, _ = staff_client(permissions=["exams:write"])

    assert_error(client.patch(_exam_url(exam.id), json={}), 422, "validation_error")
    assert_error(
        client.patch(_exam_url(exam.id), json={"status": "published"}), 422, "validation_error"
    )
    assert_error(client.patch(_exam_url("abc"), json={"name": "x"}), 422, "validation_error")
    # 兩個範圍都清空 → service 的範圍檢查（422 exam_scope_required）
    assert_error(
        client.patch(_exam_url(exam.id), json={"grade_level": None, "class_id": None}),
        422,
        "exam_scope_required",
    )


def test_admin_exams_update_401(api_client: TestClient, assert_error: AssertError) -> None:
    assert_error(api_client.patch(_exam_url(uuid4()), json={"name": "x"}), 401, "unauthenticated")


def test_admin_exams_update_403(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    exam = make_exam(db_session, name="原名")
    client, _ = staff_client(permissions=["exams:read"])

    resp = client.patch(_exam_url(exam.id), json={"name": "x"})

    assert_error(resp, 403, "permission_denied")
    db_session.expire_all()
    assert exam.name == "原名"


def test_admin_exams_update_409(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    class_a = make_class(db_session, grade_levels=(3,))
    published = make_exam(db_session, status="published")
    client, _ = staff_client(permissions=["exams:write"])

    resp = client.patch(_exam_url(published.id), json={"class_id": str(class_a.id)})

    assert_error(resp, 409, "exam_published")
    assert_error(client.patch(_exam_url(uuid4()), json={"name": "x"}), 404, "exam_not_found")
    # 已發布仍可改名稱
    assert client.patch(_exam_url(published.id), json={"name": "改名"}).status_code == 200


def _cell(student: object, subject: Subject, **fields: Any) -> dict[str, Any]:
    return {"student_id": str(student), "subject_id": str(subject.id), **fields}


def test_admin_exam_scores_put_success(
    staff_client: StaffClientFactory, db_session: Session
) -> None:
    class_a = make_class(db_session, grade_levels=(3,))
    exam = _exam_with_subjects(db_session, "國語", "數學", grade_level=None, class_=class_a)
    ming = make_student(db_session, name="王小明", class_=class_a)
    chinese, math = _subject(db_session, "國語"), _subject(db_session, "數學")
    client, _ = staff_client(permissions=["exams:write", "exams:read"])

    resp = client.put(
        _exam_url(exam.id, "/scores"),
        json={
            "cells": [
                _cell(ming.id, chinese, score=95),
                _cell(ming.id, math, is_absent=True, note="病假"),
            ]
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["written"] == 2
    assert set(body) == {"written", "changed", "renotified_students"}
    db_session.expire_all()
    rows = {
        r.subject_id: r
        for r in db_session.execute(
            select(ExamScore).where(ExamScore.exam_id == exam.id, ExamScore.student_id == ming.id)
        ).scalars()
    }
    assert rows[chinese.id].score == Decimal("95")
    assert (rows[math.id].is_absent, rows[math.id].score, rows[math.id].note) == (
        True,
        None,
        "病假",
    )
    grid = client.get(_exam_url(exam.id, "/scores")).json()
    assert len(grid["cells"]) == 2


def test_admin_exam_scores_put_422(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    class_a = make_class(db_session, grade_levels=(3,))
    exam = _exam_with_subjects(db_session, "國語", grade_level=None, class_=class_a)
    ming = make_student(db_session, class_=class_a)
    chinese = _subject(db_session, "國語")
    client, _ = staff_client(permissions=["exams:write"])

    empty = client.put(_exam_url(exam.id, "/scores"), json={"cells": []})
    over = client.put(
        _exam_url(exam.id, "/scores"), json={"cells": [_cell(ming.id, chinese, score=120)]}
    )

    assert_error(empty, 422, "validation_error")
    assert_error(over, 422, "invalid_score_cells")
    details = over.json()["error"]["details"]
    assert details[0]["code"] == "score_out_of_range"
    assert details[0]["student_id"] == str(ming.id)
    assert db_session.execute(select(ExamScore).where(ExamScore.exam_id == exam.id)).first() is None


def test_admin_exam_scores_put_401(
    api_client: TestClient, db_session: Session, assert_error: AssertError
) -> None:
    chinese = _subject(db_session, "國語")
    resp = api_client.put(
        _exam_url(uuid4(), "/scores"), json={"cells": [_cell(uuid4(), chinese, score=1)]}
    )

    assert_error(resp, 401, "unauthenticated")


def test_admin_exam_scores_put_403(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    exam = make_exam(db_session)
    chinese = _subject(db_session, "國語")
    client, _ = staff_client(permissions=["exams:read"])

    resp = client.put(
        _exam_url(exam.id, "/scores"), json={"cells": [_cell(uuid4(), chinese, score=1)]}
    )

    assert_error(resp, 403, "permission_denied")
    assert resp.json()["error"]["details"] == {"required": ["exams:write"]}


def test_admin_exam_scores_put_published_audit(
    staff_client: StaffClientFactory, db_session: Session
) -> None:
    class_a = make_class(db_session, grade_levels=(3,))
    exam = _exam_with_subjects(
        db_session, "國語", "數學", grade_level=None, class_=class_a, status="published"
    )
    chinese, math = _subject(db_session, "國語"), _subject(db_session, "數學")
    ming = make_student(db_session, class_=class_a)
    make_exam_score(db_session, exam, ming, chinese, score=Decimal("95"))
    make_exam_score(db_session, exam, ming, math, score=Decimal("80"))
    client, staff = staff_client(permissions=["exams:write"])

    resp = client.put(
        _exam_url(exam.id, "/scores"),
        json={"cells": [_cell(ming.id, chinese, score=97), _cell(ming.id, math, score=80)]},
    )

    assert resp.status_code == 200
    assert resp.json()["written"] == 2
    assert resp.json()["changed"] == 1
    logs = list(
        db_session.execute(
            select(AuditLog).where(
                AuditLog.action == "exam_score.update", AuditLog.actor_id == staff.id
            )
        ).scalars()
    )
    assert len(logs) == 1
    assert logs[0].ip is None or isinstance(logs[0].ip, str)
    db_session.expire_all()
    changed = db_session.execute(
        select(ExamScore).where(
            ExamScore.exam_id == exam.id,
            ExamScore.student_id == ming.id,
            ExamScore.subject_id == chinese.id,
        )
    ).scalar_one()
    assert changed.score == Decimal("97")


def test_admin_exam_publish_success(staff_client: StaffClientFactory, db_session: Session) -> None:
    class_a = make_class(db_session, grade_levels=(3,))
    exam = _exam_with_subjects(db_session, "國語", grade_level=None, class_=class_a)
    ming = make_student(db_session, name="王小明", class_=class_a)
    make_guardian(db_session, ming, parent=make_parent(db_session))
    client, staff = staff_client(permissions=["exams:publish"], display_name="林主任")

    resp = client.post(_exam_url(exam.id, "/publish"))

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "published"
    assert body["published_at"] is not None
    assert body["published_by_name"] == "林主任"
    db_session.expire_all()
    assert (exam.status, exam.published_by) == ("published", staff.id)
    # 家長收到成績公布通知（已 commit）
    notifications = list(
        db_session.execute(
            select(Notification).where(Notification.event == "exam.published")
        ).scalars()
    )
    assert len(notifications) == 1
    assert notifications[0].payload["exam_id"] == str(exam.id)


def test_admin_exam_publish_422(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    empty = make_exam(db_session)
    client, _ = staff_client(permissions=["exams:publish"])

    assert_error(client.post(_exam_url("abc", "/publish")), 422, "validation_error")
    assert_error(client.post(_exam_url(empty.id, "/publish")), 422, "exam_has_no_subjects")
    db_session.expire_all()
    assert empty.status == "draft"


def test_admin_exam_publish_401(api_client: TestClient, assert_error: AssertError) -> None:
    assert_error(api_client.post(_exam_url(uuid4(), "/publish")), 401, "unauthenticated")


def test_admin_exam_publish_403(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    exam = _exam_with_subjects(db_session, "國語")
    client, _ = staff_client(permissions=["exams:write"])

    resp = client.post(_exam_url(exam.id, "/publish"))

    assert_error(resp, 403, "permission_denied")
    assert resp.json()["error"]["details"] == {"required": ["exams:publish"]}
    db_session.expire_all()
    assert exam.status == "draft"


def test_admin_exam_publish_409(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    published = _exam_with_subjects(db_session, "國語", status="published")
    client, _ = staff_client(permissions=["exams:publish"])

    assert_error(client.post(_exam_url(published.id, "/publish")), 409, "exam_already_published")
    assert_error(client.post(_exam_url(uuid4(), "/publish")), 404, "exam_not_found")


def test_admin_exams_write_guard_registered(app: FastAPI) -> None:
    assert admin_routes_without_permission(app) == []
    paths = app.openapi()["paths"]
    assert "patch" in paths[_URL + "/{exam_id}"]
    assert "put" in paths[_URL + "/{exam_id}/scores"]
    assert "post" in paths[_URL + "/{exam_id}/publish"]
