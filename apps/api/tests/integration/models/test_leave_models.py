"""BACKEND-340：app/models/leaves.py（StudentLeave、StudentLeaveAttachment）與請假 factory。"""

from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.leaves import EXCLUSION_LEAVE_OVERLAP, LEAVE_TYPE_LABELS
from tests.integration.test_schema_drift import PENDING_MODEL_TABLES
from tests.support.factories import make_leave, make_leave_attachment, make_student


def test_leave_models_roundtrip(db_session: Session) -> None:
    student = make_student(db_session)
    leave = make_leave(db_session, student, start_date=date(2026, 9, 1), end_date=date(2026, 9, 2))
    db_session.refresh(leave)

    assert leave.status == "active"
    assert leave.leave_type == "sick"
    assert leave.created_by_type == "staff"
    assert (leave.start_date, leave.end_date) == (date(2026, 9, 1), date(2026, 9, 2))
    assert leave.cancelled_at is None
    assert leave.attachments == []

    attachment = make_leave_attachment(db_session, leave)
    db_session.expire(leave)

    assert [a.id for a in leave.attachments] == [attachment.id]
    assert attachment.mime_type == "application/pdf"
    assert attachment.storage_path.startswith(f"{leave.id}/")
    assert attachment.storage_path.endswith(".pdf")


def test_leave_models_defaults_and_constants(db_session: Session) -> None:
    leave = make_leave(db_session, make_student(db_session), start_date=date(2026, 9, 1))

    assert leave.end_date == date(2026, 9, 1)  # factory 預設單日
    assert LEAVE_TYPE_LABELS == {"sick": "病假", "personal": "事假", "other": "其他"}
    assert EXCLUSION_LEAVE_OVERLAP == "ex_student_leaves_no_overlap"


def test_leave_models_cancelled_factory_fills_cancel_columns(db_session: Session) -> None:
    leave = make_leave(
        db_session, make_student(db_session), start_date=date(2026, 9, 1), status="cancelled"
    )

    assert leave.status == "cancelled"
    assert leave.cancelled_at is not None
    assert leave.cancelled_by_type == leave.created_by_type
    assert leave.cancelled_by_id == leave.created_by_id


def test_leave_models_overlap_exclusion(db_session: Session) -> None:
    student = make_student(db_session)
    make_leave(db_session, student, start_date=date(2026, 9, 1), end_date=date(2026, 9, 3))

    with pytest.raises(IntegrityError) as excinfo, db_session.begin_nested():
        make_leave(db_session, student, start_date=date(2026, 9, 3), end_date=date(2026, 9, 4))

    assert excinfo.value.orig is not None
    assert getattr(excinfo.value.orig, "sqlstate", None) == "23P01"
    diag = getattr(excinfo.value.orig, "diag", None)
    assert diag is not None
    assert diag.constraint_name == EXCLUSION_LEAVE_OVERLAP

    # 其他學生同日不受影響；相鄰不重疊的日期可建
    make_leave(db_session, make_student(db_session), start_date=date(2026, 9, 3))
    make_leave(db_session, student, start_date=date(2026, 9, 4), end_date=date(2026, 9, 5))


def test_leave_models_cancelled_not_blocking(db_session: Session) -> None:
    student = make_student(db_session)
    make_leave(
        db_session,
        student,
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 3),
        status="cancelled",
    )

    second = make_leave(db_session, student, start_date=date(2026, 9, 2))

    assert second.status == "active"


def test_leave_models_not_pending() -> None:
    assert "student_leaves" not in PENDING_MODEL_TABLES
    assert "student_leave_attachments" not in PENDING_MODEL_TABLES
