"""BACKEND-301：出勤 schemas。"""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.attendance import (
    STATUS_LABELS,
    AttendanceAmendIn,
    AttendanceRowOut,
    BatchCheckInIn,
    CheckInIn,
    CheckOutIn,
    DailyAttendanceQuery,
    MarkAbsentIn,
    MonthlyAttendanceQuery,
    ParentAttendanceEventOut,
    to_parent_attendance_event,
)


def test_attendance_schemas_status_labels() -> None:
    assert STATUS_LABELS == {
        "expected": "預計到班",
        "present": "已到班",
        "left": "已離班",
        "absent": "缺席",
        "leave": "請假",
    }


def test_attendance_schemas_batch_ids() -> None:
    u1, u2 = uuid4(), uuid4()
    with pytest.raises(ValidationError):
        BatchCheckInIn(student_ids=[u1, u1])
    with pytest.raises(ValidationError):
        BatchCheckInIn(student_ids=[])
    with pytest.raises(ValidationError):
        BatchCheckInIn(student_ids=[uuid4() for _ in range(201)])
    assert BatchCheckInIn(student_ids=[u1, u2]).student_ids == [u1, u2]
    assert len(BatchCheckInIn(student_ids=[uuid4() for _ in range(200)]).student_ids) == 200


def test_attendance_schemas_amend_requires_change() -> None:
    with pytest.raises(ValidationError):
        AttendanceAmendIn.model_validate({"reason": "誤刷"})
    m = AttendanceAmendIn.model_validate({"reason": "誤刷", "note": None})
    assert m.model_fields_set == {"reason", "note"}
    assert m.note is None
    with pytest.raises(ValidationError):
        AttendanceAmendIn.model_validate({"note": "x"})
    with pytest.raises(ValidationError):
        AttendanceAmendIn.model_validate({"reason": "", "note": "x"})
    with pytest.raises(ValidationError):
        AttendanceAmendIn.model_validate({"reason": "r" * 201, "note": "x"})


def test_attendance_schemas_amend_rejects_leave_and_naive() -> None:
    with pytest.raises(ValidationError):
        AttendanceAmendIn.model_validate({"reason": "x", "status": "leave"})
    with pytest.raises(ValidationError):
        AttendanceAmendIn.model_validate({"reason": "x", "check_in_at": "2026-09-01T15:00:00"})
    ok = AttendanceAmendIn.model_validate(
        {"reason": "x", "check_in_at": "2026-09-01T15:00:00+08:00", "check_out_at": None}
    )
    assert ok.check_in_at == datetime(2026, 9, 1, 7, tzinfo=UTC)
    assert ok.check_out_at is None
    assert ok.model_fields_set == {"reason", "check_in_at", "check_out_at"}


def test_attendance_schemas_month_pattern() -> None:
    with pytest.raises(ValidationError):
        MonthlyAttendanceQuery(month="2026-13")
    with pytest.raises(ValidationError):
        MonthlyAttendanceQuery(month="2026-00")
    assert MonthlyAttendanceQuery(month="2026-09").class_id is None


def test_attendance_schemas_extra_forbid() -> None:
    with pytest.raises(ValidationError):
        CheckInIn.model_validate({"note": "x", "time": "15:00"})
    for cls in (CheckInIn, CheckOutIn, MarkAbsentIn):
        assert cls().note is None
        with pytest.raises(ValidationError):
            cls(note="n" * 201)
    with pytest.raises(ValidationError):
        DailyAttendanceQuery.model_validate({"date": "2026-09-01", "x": 1})
    with pytest.raises(ValidationError):
        DailyAttendanceQuery(status="late")
    assert DailyAttendanceQuery().date is None


def test_attendance_schemas_row_allows_virtual_row() -> None:
    row = AttendanceRowOut(
        id=None,
        student_id=uuid4(),
        student_no="S001",
        student_name="王小明",
        grade_level=3,
        class_id=None,
        class_name=None,
        service_date=date(2026, 9, 1),
        status="expected",
        check_in_at=None,
        check_in_source=None,
        check_out_at=None,
        check_out_source=None,
        leave=None,
        note=None,
        updated_at=None,
    )
    assert row.id is None


def test_attendance_schemas_parent_event_is_trimmed() -> None:
    sid = uuid4()

    class _Row:
        student_id = sid
        service_date = date(2026, 9, 1)
        status = "present"
        check_in_at = datetime(2026, 9, 1, 8, tzinfo=UTC)
        check_out_at = None
        note = "內部備註"
        student_name = "王小明"

    event = to_parent_attendance_event(_Row())
    assert set(event) == {"student_id", "service_date", "status", "check_in_at", "check_out_at"}
    assert event["student_id"] == str(sid)
    assert event["service_date"] == "2026-09-01"
    assert event["check_in_at"] == "2026-09-01T08:00:00Z"
    assert event["check_out_at"] is None
    assert set(ParentAttendanceEventOut.model_fields) == set(event)
