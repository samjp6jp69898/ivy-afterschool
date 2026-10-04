"""BACKEND-370：app/models/homework.py（HomeworkItem、HomeworkDailyProgress）與作業 factory。"""

from datetime import date, time

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.homework import HomeworkDailyProgress, HomeworkItem
from app.models.reference import Subject
from tests.integration.test_schema_drift import PENDING_MODEL_TABLES
from tests.support.factories import make_homework_item, make_homework_progress, make_student


def test_homework_models_roundtrip(db_session: Session) -> None:
    student = make_student(db_session)
    item = make_homework_item(db_session, student, service_date=date(2026, 9, 1))
    progress = make_homework_progress(
        db_session, student, service_date=date(2026, 9, 1), ready_eta=time(17, 30)
    )
    db_session.expire_all()

    loaded = db_session.execute(select(HomeworkItem).where(HomeworkItem.id == item.id)).scalar_one()
    assert loaded.status == "todo"
    assert loaded.sort_order == 0
    assert loaded.title == "數學習作 p.12-13"
    assert loaded.subject is None
    assert loaded.updated_by is None

    loaded_progress = db_session.execute(
        select(HomeworkDailyProgress).where(HomeworkDailyProgress.id == progress.id)
    ).scalar_one()
    assert loaded_progress.ready_eta == time(17, 30)
    assert isinstance(loaded_progress.ready_eta, time)
    assert loaded_progress.eta_updated_at is not None
    assert loaded_progress.overall_status == "not_started"
    assert loaded_progress.note is None


def test_homework_models_subject_joined(db_session: Session) -> None:
    math = db_session.execute(select(Subject).where(Subject.name == "數學")).scalar_one()
    item = make_homework_item(
        db_session, make_student(db_session), service_date=date(2026, 9, 1), subject=math
    )
    db_session.expire_all()

    loaded = db_session.execute(select(HomeworkItem).where(HomeworkItem.id == item.id)).scalar_one()

    assert loaded.subject is not None
    assert loaded.subject.name == "數學"
    assert loaded.subject_id == math.id


def test_homework_models_progress_without_eta(db_session: Session) -> None:
    progress = make_homework_progress(
        db_session, make_student(db_session), service_date=date(2026, 9, 1), note="明天要考試"
    )

    assert progress.ready_eta is None
    assert progress.eta_updated_at is None
    assert progress.note == "明天要考試"


def test_homework_models_progress_unique(db_session: Session) -> None:
    student = make_student(db_session)
    make_homework_progress(db_session, student, service_date=date(2026, 9, 1))

    with pytest.raises(IntegrityError) as excinfo, db_session.begin_nested():
        make_homework_progress(db_session, student, service_date=date(2026, 9, 1))

    diag = getattr(excinfo.value.orig, "diag", None)
    assert diag is not None
    assert diag.constraint_name == "uq_homework_daily_progress_student_date"
    # 不同日期可以
    make_homework_progress(db_session, student, service_date=date(2026, 9, 2))


def test_homework_models_eta_audit_check(db_session: Session) -> None:
    student = make_student(db_session)

    with pytest.raises(IntegrityError) as excinfo, db_session.begin_nested():
        db_session.add(
            HomeworkDailyProgress(
                student_id=student.id,
                service_date=date(2026, 9, 1),
                ready_eta=time(17, 0),
                eta_updated_at=None,
            )
        )
        db_session.flush()

    diag = getattr(excinfo.value.orig, "diag", None)
    assert diag is not None
    assert diag.constraint_name == "ck_homework_daily_progress_eta_audit"


def test_homework_models_not_pending() -> None:
    assert "homework_items" not in PENDING_MODEL_TABLES
    assert "homework_daily_progress" not in PENDING_MODEL_TABLES
