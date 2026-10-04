"""BACKEND-450：app/models/exams.py（Exam、ExamSubject、ExamScore）與成績 factory。"""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.exams import Exam, ExamScore
from app.models.reference import Subject
from tests.integration.test_schema_drift import PENDING_MODEL_TABLES
from tests.support.factories import (
    make_class,
    make_exam,
    make_exam_score,
    make_exam_subject,
    make_student,
)


def _subject(db: Session, name: str) -> Subject:
    return db.execute(select(Subject).where(Subject.name == name)).scalar_one()


def _constraint(exc: IntegrityError) -> str | None:
    diag = getattr(exc.orig, "diag", None)
    return None if diag is None else diag.constraint_name


def test_exam_models_roundtrip(db_session: Session) -> None:
    math = _subject(db_session, "數學")
    exam = make_exam(db_session)
    make_exam_subject(db_session, exam, math, full_score=Decimal("100"))
    student = make_student(db_session)
    score = make_exam_score(db_session, exam, student, math, score=Decimal("95.5"))
    db_session.expire_all()

    loaded_score = db_session.execute(
        select(ExamScore).where(ExamScore.id == score.id)
    ).scalar_one()
    assert loaded_score.score == Decimal("95.50")
    assert isinstance(loaded_score.score, Decimal)
    assert loaded_score.is_absent is False

    loaded = db_session.execute(select(Exam).where(Exam.id == exam.id)).scalar_one()
    assert loaded.status == "draft"
    assert loaded.published_at is None
    assert loaded.exam_type.name == "段考"
    assert loaded.school_class is None
    assert [s.subject.name for s in loaded.subjects] == ["數學"]
    assert loaded.subjects[0].full_score == Decimal("100.00")
    assert isinstance(loaded.subjects[0].full_score, Decimal)


def test_exam_models_subjects_ordered_by_sort_order(db_session: Session) -> None:
    exam = make_exam(db_session)
    make_exam_subject(db_session, exam, _subject(db_session, "英語"), sort_order=2)
    make_exam_subject(db_session, exam, _subject(db_session, "數學"), sort_order=1)
    db_session.expire_all()

    loaded = db_session.execute(select(Exam).where(Exam.id == exam.id)).scalar_one()

    assert [s.subject.name for s in loaded.subjects] == ["數學", "英語"]


def test_exam_models_factory_scope_and_published(db_session: Session) -> None:
    klass = make_class(db_session)
    published = make_exam(db_session, class_=klass, grade_level=None, status="published")
    default = make_exam(db_session)

    assert published.class_id == klass.id
    assert published.grade_level is None
    assert published.published_at is not None
    assert default.grade_level == 3
    assert default.exam_date == date(2026, 10, 15)


def test_exam_models_score_exceeds_full(db_session: Session) -> None:
    math = _subject(db_session, "數學")
    exam = make_exam(db_session)
    make_exam_subject(db_session, exam, math, full_score=Decimal("100"))
    student = make_student(db_session)

    with pytest.raises(IntegrityError) as over, db_session.begin_nested():
        make_exam_score(db_session, exam, student, math, score=Decimal("101"))
    assert getattr(over.value.orig, "sqlstate", None) == "23514"

    with pytest.raises(IntegrityError) as absent, db_session.begin_nested():
        make_exam_score(db_session, exam, student, math, score=Decimal("0"), is_absent=True)
    assert _constraint(absent.value) == "ck_exam_scores_absent_no_score"

    # 滿分本身可以；缺考且無分數可以
    make_exam_score(db_session, exam, student, math, score=Decimal("100"))
    other = make_student(db_session)
    absent_ok = make_exam_score(db_session, exam, other, math, score=None, is_absent=True)
    assert absent_ok.is_absent is True
    assert absent_ok.score is None


def test_exam_models_subject_not_in_exam(db_session: Session) -> None:
    exam = make_exam(db_session)
    make_exam_subject(db_session, exam, _subject(db_session, "數學"))
    student = make_student(db_session)

    with pytest.raises(IntegrityError) as excinfo, db_session.begin_nested():
        make_exam_score(db_session, exam, student, _subject(db_session, "英語"))

    assert getattr(excinfo.value.orig, "sqlstate", None) == "23503"
    assert _constraint(excinfo.value) == "fk_exam_scores_exam_subject"


def test_exam_models_score_unique_per_student_subject(db_session: Session) -> None:
    math = _subject(db_session, "數學")
    exam = make_exam(db_session)
    make_exam_subject(db_session, exam, math)
    student = make_student(db_session)
    make_exam_score(db_session, exam, student, math)

    with pytest.raises(IntegrityError) as excinfo, db_session.begin_nested():
        make_exam_score(db_session, exam, student, math)

    assert _constraint(excinfo.value) == "uq_exam_scores_exam_student_subject"


def test_exam_models_subject_unique_per_exam(db_session: Session) -> None:
    math = _subject(db_session, "數學")
    exam = make_exam(db_session)
    make_exam_subject(db_session, exam, math)

    with pytest.raises(IntegrityError) as excinfo, db_session.begin_nested():
        make_exam_subject(db_session, exam, math)

    assert _constraint(excinfo.value) == "uq_exam_subjects_exam_subject"


def test_exam_models_not_pending() -> None:
    for table in ("exams", "exam_subjects", "exam_scores"):
        assert table not in PENDING_MODEL_TABLES
