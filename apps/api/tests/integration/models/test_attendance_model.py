"""BACKEND-300：app/models/attendance.py（StudentAttendance）與 make_attendance factory。"""

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Base
from app.models.attendance import ATTENDANCE_STATUSES, CHECKED_IN_STATUSES, StudentAttendance
from tests.integration.test_schema_drift import PENDING_MODEL_TABLES
from tests.support.factories import make_attendance, make_leave, make_student

_DAY = date(2026, 9, 1)


def _constraint(exc: IntegrityError) -> str | None:
    diag = getattr(exc.orig, "diag", None)
    return None if diag is None else diag.constraint_name


def test_attendance_model_roundtrip(db_session: Session) -> None:
    student = make_student(db_session)
    row = StudentAttendance(student_id=student.id, service_date=_DAY)
    db_session.add(row)
    db_session.flush()
    db_session.refresh(row)

    assert row.status == "expected"
    assert row.check_in_at is None
    assert row.check_out_at is None
    assert row.check_in_source is None
    assert row.leave_id is None
    assert row.note is None
    assert row.updated_by is None
    loaded = db_session.execute(
        select(StudentAttendance).where(StudentAttendance.id == row.id)
    ).scalar_one()
    assert (loaded.student_id, loaded.service_date) == (student.id, _DAY)


def test_attendance_model_constants() -> None:
    assert ATTENDANCE_STATUSES == ("expected", "present", "left", "absent", "leave")
    assert frozenset({"present", "left"}) == CHECKED_IN_STATUSES


def test_attendance_model_unique(db_session: Session) -> None:
    student = make_student(db_session)
    make_attendance(db_session, student, service_date=_DAY)

    with pytest.raises(IntegrityError) as excinfo, db_session.begin_nested():
        make_attendance(db_session, student, service_date=_DAY)

    assert _constraint(excinfo.value) == "uq_student_attendances_student_date"
    # 其他學生同日、同學生他日都可以
    make_attendance(db_session, make_student(db_session), service_date=_DAY)
    make_attendance(db_session, student, service_date=date(2026, 9, 2))


def test_attendance_model_leave_link_check(db_session: Session) -> None:
    student = make_student(db_session)
    db_session.add(StudentAttendance(student_id=student.id, service_date=_DAY, status="leave"))

    with pytest.raises(IntegrityError) as excinfo, db_session.begin_nested():
        db_session.flush()

    assert _constraint(excinfo.value) == "ck_student_attendances_leave_link"


def test_attendance_model_factory_leave(db_session: Session) -> None:
    student = make_student(db_session)
    leave = make_leave(db_session, student, start_date=_DAY)

    row = make_attendance(db_session, student, service_date=_DAY, status="leave", leave=leave)

    assert row.leave_id == leave.id
    with pytest.raises(ValueError, match="leave"):
        make_attendance(db_session, make_student(db_session), service_date=_DAY, status="leave")


def test_attendance_model_factory_left(db_session: Session) -> None:
    student = make_student(db_session)

    row = make_attendance(db_session, student, service_date=_DAY, status="left")

    assert row.check_in_at == datetime(2026, 9, 1, 7, 0, tzinfo=UTC)
    assert row.check_out_at == datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
    assert row.check_in_source == "manual"
    assert row.check_out_source == "manual"


def test_attendance_model_factory_present_and_absent(db_session: Session) -> None:
    present = make_attendance(
        db_session, make_student(db_session), service_date=_DAY, status="present"
    )
    absent = make_attendance(
        db_session, make_student(db_session), service_date=_DAY, status="absent"
    )

    assert present.check_in_at == datetime(2026, 9, 1, 7, 0, tzinfo=UTC)
    assert present.check_out_at is None
    assert present.check_out_source is None
    assert absent.check_in_at is None
    assert absent.check_in_source is None


def test_attendance_model_not_pending() -> None:
    assert "student_attendances" not in PENDING_MODEL_TABLES
    assert "student_attendances" in Base.metadata.tables
