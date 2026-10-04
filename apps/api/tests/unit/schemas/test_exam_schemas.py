"""BACKEND-451：考試成績 schemas。"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.classes import ClassBriefOut
from app.schemas.exams import (
    ExamCreateIn,
    ExamOut,
    ExamSubjectIn,
    ExamSubjectsPutIn,
    ExamUpdateIn,
    ScoreCellIn,
    ScoreCellOut,
    ScoresPutIn,
    SubjectSummaryOut,
)

U = uuid4()


def test_exam_schemas_scope_required() -> None:
    with pytest.raises(ValidationError):
        ExamCreateIn(name="第一次段考", exam_type_id=U, exam_date=date(2026, 10, 15))
    assert (
        ExamCreateIn(
            name="第一次段考", exam_type_id=U, exam_date=date(2026, 10, 15), grade_level=3
        ).grade_level
        == 3
    )
    assert (
        ExamCreateIn(
            name="第一次段考", exam_type_id=U, exam_date=date(2026, 10, 15), class_id=U
        ).class_id
        == U
    )


def test_exam_schemas_create_limits() -> None:
    base = {"exam_type_id": U, "exam_date": date(2026, 10, 15), "grade_level": 3}
    with pytest.raises(ValidationError):
        ExamCreateIn(name="", **base)
    with pytest.raises(ValidationError):
        ExamCreateIn(name="考" * 51, **base)
    with pytest.raises(ValidationError):
        ExamCreateIn(name="x", note="n" * 501, **base)
    for level in (0, 7):
        with pytest.raises(ValidationError):
            ExamCreateIn(name="x", **{**base, "grade_level": level})
    with pytest.raises(ValidationError):
        ExamCreateIn.model_validate({"name": "x", **{k: str(v) for k, v in base.items()}, "y": 1})


def test_exam_schemas_update() -> None:
    with pytest.raises(ValidationError):
        ExamUpdateIn.model_validate({})
    m = ExamUpdateIn.model_validate({"grade_level": None})
    assert m.model_fields_set == {"grade_level"}
    assert m.grade_level is None
    with pytest.raises(ValidationError):
        ExamUpdateIn.model_validate({"name": None})


def test_exam_schemas_subjects_unique() -> None:
    with pytest.raises(ValidationError):
        ExamSubjectsPutIn(items=[{"subject_id": U}, {"subject_id": U}])
    with pytest.raises(ValidationError):
        ExamSubjectsPutIn(items=[])
    with pytest.raises(ValidationError):
        ExamSubjectIn(subject_id=U, full_score=Decimal("0"))
    with pytest.raises(ValidationError):
        ExamSubjectIn(subject_id=U, full_score=Decimal("1000.01"))
    with pytest.raises(ValidationError):
        ExamSubjectIn(subject_id=U, full_score=Decimal("10.001"))
    item = ExamSubjectIn(subject_id=U)
    assert (item.full_score, item.sort_order) == (Decimal("100"), 0)
    assert ExamSubjectIn(subject_id=U, full_score=Decimal("1000")).full_score == Decimal("1000")
    with pytest.raises(ValidationError):
        ExamSubjectsPutIn(items=[{"subject_id": uuid4()} for _ in range(21)])
    assert len(ExamSubjectsPutIn(items=[{"subject_id": uuid4()} for _ in range(20)]).items) == 20


def test_exam_schemas_score_format() -> None:
    with pytest.raises(ValidationError):
        ScoreCellIn(student_id=U, subject_id=U, score=Decimal("95.555"))
    with pytest.raises(ValidationError):
        ScoreCellIn(student_id=U, subject_id=U, score=Decimal("-1"))
    with pytest.raises(ValidationError):
        ScoreCellIn(student_id=U, subject_id=U, score=Decimal("1000.01"))
    cell = ScoreCellIn(student_id=U, subject_id=U, score=None, is_absent=True)
    assert (cell.score, cell.is_absent) == (None, True)
    assert ScoreCellIn(student_id=U, subject_id=U, score=Decimal("95.55")).score == Decimal("95.55")
    assert ScoreCellIn(student_id=U, subject_id=U, score=Decimal("0")).is_absent is False
    with pytest.raises(ValidationError):
        ScoreCellIn(student_id=U, subject_id=U, note="n" * 201)


def test_exam_schemas_scores_put() -> None:
    cell = {"student_id": U, "subject_id": U, "score": "90"}
    with pytest.raises(ValidationError):
        ScoresPutIn(cells=[])
    with pytest.raises(ValidationError):
        ScoresPutIn(cells=[cell] * 2001)
    put = ScoresPutIn(cells=[cell] * 2000)
    assert (len(put.cells), put.notify_parents) == (2000, False)
    with pytest.raises(ValidationError):
        ScoresPutIn.model_validate({"cells": [cell], "x": 1})


def test_exam_schemas_decimal_json() -> None:
    out = ScoreCellOut(
        student_id=U,
        subject_id=U,
        score=Decimal("95.50"),
        is_absent=False,
        note=None,
        updated_at=datetime(2026, 9, 1, tzinfo=UTC),
    )
    assert '"score":95.5' in out.model_dump_json()
    none_out = out.model_copy(update={"score": None})
    assert '"score":null' in none_out.model_dump_json()
    summary = SubjectSummaryOut(
        subject_id=U,
        subject_name="數學",
        full_score=Decimal("100.00"),
        scored_count=2,
        absent_count=0,
        missing_count=1,
        average=Decimal("90.25"),
        max=Decimal("95"),
        min=Decimal("85.5"),
    )
    dumped = summary.model_dump_json()
    assert '"full_score":100.0' in dumped
    assert '"average":90.25' in dumped
    assert '"min":85.5' in dumped


def test_exam_schemas_class_alias() -> None:
    cid = uuid4()
    exam = ExamOut(
        id=uuid4(),
        name="第一次段考",
        exam_type={"id": U, "name": "段考"},
        exam_date=date(2026, 10, 15),
        grade_level=None,
        class_=ClassBriefOut(id=cid, name="A 班"),
        status="draft",
        published_at=None,
        published_by_name=None,
        note=None,
        subjects=[],
        roster_count=0,
        created_at=datetime(2026, 9, 1, tzinfo=UTC),
    )
    dumped = exam.model_dump(by_alias=True)
    assert dumped["class"]["name"] == "A 班"
    assert "class_" not in dumped
    assert '"class":{' in exam.model_dump_json(by_alias=True)


def test_exam_schemas_subject_sort_order_upper_bound() -> None:
    assert ExamSubjectIn(subject_id=U, sort_order=2147483647).sort_order == 2147483647
    with pytest.raises(ValidationError):
        ExamSubjectIn(subject_id=U, sort_order=2147483648)
