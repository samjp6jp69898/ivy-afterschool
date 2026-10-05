"""leave_attendance（請假與出勤連動）。

- BACKEND-342：apply_attendance_for_leave（請假生效時把期間內出勤改為 leave）。
- BACKEND-343：revert_attendance_for_leave（取消請假時把出勤改回 expected）。

營業時段讀 seed 預設（週一到週五營業、週六不營業、週日不列）；每個測試前後清空設定快取。
"""

from collections.abc import Iterator
from datetime import date
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.core.tx_hooks import install_tx_hooks
from app.models.attendance import StudentAttendance
from app.models.reference import ClosedDay
from app.realtime import publish as publish_module
from app.realtime.publish import admin_topic_channel
from app.services.leave_attendance import (
    LeaveApplyResult,
    apply_attendance_for_leave,
    revert_attendance_for_leave,
)
from app.services.settings_service import clear_settings_cache
from tests.support.factories import make_attendance, make_leave, make_staff, make_student
from tests.support.fake_clock import FakeClock

Call = tuple[list[str], dict[str, Any]]


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    clear_settings_cache()
    yield
    clear_settings_cache()


@pytest.fixture
def published(monkeypatch: pytest.MonkeyPatch) -> list[Call]:
    install_tx_hooks()
    calls: list[Call] = []

    def record(channels: list[str], message: dict[str, Any]) -> None:
        calls.append((list(channels), dict(message)))

    monkeypatch.setattr(publish_module, "publish_threadsafe", record)
    return calls


def _rows(session: Session, student_id: UUID) -> dict[date, StudentAttendance]:
    rows = session.execute(
        select(StudentAttendance)
        .where(StudentAttendance.student_id == student_id)
        .execution_options(populate_existing=True)
    ).scalars()
    return {row.service_date: row for row in rows}


@pytest.mark.clock("2026-09-03T10:00:00+08:00")
def test_apply_leave_marks_attendance(db_session: Session, fake_clock: FakeClock) -> None:
    student = make_student(db_session)
    staff = make_staff(db_session)
    make_attendance(db_session, student, service_date=date(2026, 9, 1))
    make_attendance(
        db_session, student, service_date=date(2026, 9, 2), status="absent", note="未到"
    )
    leave = make_leave(db_session, student, start_date=date(2026, 9, 1), end_date=date(2026, 9, 4))

    result = apply_attendance_for_leave(
        db_session, leave, actor_staff_id=staff.id, clock=fake_clock
    )

    assert result == LeaveApplyResult(
        applied=[date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3)], skipped_checked_in=[]
    )
    rows = _rows(db_session, student.id)
    assert sorted(rows) == [date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3)]  # 9/4 未來無列
    for d in (date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3)):
        assert (rows[d].status, rows[d].leave_id, rows[d].updated_by) == (
            "leave",
            leave.id,
            staff.id,
        )
        assert (rows[d].check_in_at, rows[d].check_out_at) == (None, None)


@pytest.mark.clock("2026-09-03T10:00:00+08:00")
def test_apply_leave_keeps_checked_in(db_session: Session, fake_clock: FakeClock) -> None:
    student = make_student(db_session)
    present = make_attendance(db_session, student, service_date=date(2026, 9, 2), status="present")
    left = make_attendance(db_session, student, service_date=date(2026, 9, 3), status="left")
    check_in_at = present.check_in_at
    leave = make_leave(db_session, student, start_date=date(2026, 9, 1), end_date=date(2026, 9, 3))

    result = apply_attendance_for_leave(db_session, leave, actor_staff_id=None, clock=fake_clock)

    assert result.applied == [date(2026, 9, 1)]
    assert result.skipped_checked_in == [date(2026, 9, 2), date(2026, 9, 3)]
    rows = _rows(db_session, student.id)
    assert (rows[date(2026, 9, 2)].status, rows[date(2026, 9, 2)].leave_id) == ("present", None)
    assert rows[date(2026, 9, 2)].check_in_at == check_in_at
    assert (rows[date(2026, 9, 3)].id, rows[date(2026, 9, 3)].status) == (left.id, "left")
    assert (rows[date(2026, 9, 1)].status, rows[date(2026, 9, 1)].updated_by) == ("leave", None)


@pytest.mark.clock("2026-09-08T10:00:00+08:00")
def test_apply_leave_skips_closed_and_weekend(db_session: Session, fake_clock: FakeClock) -> None:
    student = make_student(db_session)
    db_session.add(ClosedDay(date=date(2026, 9, 7), reason="颱風假"))
    db_session.flush()
    leave = make_leave(db_session, student, start_date=date(2026, 9, 4), end_date=date(2026, 9, 7))

    result = apply_attendance_for_leave(db_session, leave, actor_staff_id=None, clock=fake_clock)

    assert result == LeaveApplyResult(applied=[date(2026, 9, 4)], skipped_checked_in=[])
    assert sorted(_rows(db_session, student.id)) == [date(2026, 9, 4)]


def test_apply_leave_future_only_noop(db_session: Session, fake_clock: FakeClock) -> None:
    student = make_student(db_session)
    leave = make_leave(
        db_session, student, start_date=date(2026, 9, 10), end_date=date(2026, 9, 11)
    )

    result = apply_attendance_for_leave(db_session, leave, actor_staff_id=None, clock=fake_clock)

    assert result == LeaveApplyResult(applied=[], skipped_checked_in=[])
    assert _rows(db_session, student.id) == {}


@pytest.mark.clock("2026-09-07T10:00:00+08:00")
def test_apply_leave_single_write(db_session: Session, fake_clock: FakeClock) -> None:
    student = make_student(db_session)
    make_attendance(db_session, student, service_date=date(2026, 9, 2), status="absent")
    leave = make_leave(db_session, student, start_date=date(2026, 9, 1), end_date=date(2026, 9, 7))
    statements: list[str] = []

    def record(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        statements.append(statement)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", record)
    try:
        result = apply_attendance_for_leave(
            db_session, leave, actor_staff_id=None, clock=fake_clock
        )
    finally:
        event.remove(engine, "before_cursor_execute", record)

    # 9/1~9/4 與 9/7 共 5 個營業日
    assert len(result.applied) == 5
    writes = [s for s in statements if s.lstrip().upper().startswith(("INSERT", "UPDATE"))]
    assert len(writes) == 1


@pytest.mark.clock("2026-09-03T10:00:00+08:00")
def test_apply_leave_broadcasts_after_commit(
    db_session: Session, fake_clock: FakeClock, published: list[Call]
) -> None:
    student = make_student(db_session)
    leave = make_leave(db_session, student, start_date=date(2026, 9, 2), end_date=date(2026, 9, 4))

    apply_attendance_for_leave(db_session, leave, actor_staff_id=None, clock=fake_clock)
    assert published == []
    db_session.commit()

    assert published == [
        (
            [admin_topic_channel("attendance")],
            {
                "type": "attendance.bulk_updated",
                "data": {"student_id": str(student.id), "dates": ["2026-09-02", "2026-09-03"]},
                "sent_at": "2026-09-03T02:00:00Z",
            },
        )
    ]


@pytest.mark.clock("2026-09-03T10:00:00+08:00")
def test_apply_leave_nothing_applied_no_broadcast(
    db_session: Session, fake_clock: FakeClock, published: list[Call]
) -> None:
    student = make_student(db_session)
    make_attendance(db_session, student, service_date=date(2026, 9, 3), status="present")
    leave = make_leave(db_session, student, start_date=date(2026, 9, 3), end_date=date(2026, 9, 4))

    result = apply_attendance_for_leave(db_session, leave, actor_staff_id=None, clock=fake_clock)
    db_session.commit()

    assert result == LeaveApplyResult(applied=[], skipped_checked_in=[date(2026, 9, 3)])
    assert published == []


# --- BACKEND-343 revert_attendance_for_leave ---


def test_revert_leave_attendance(
    db_session: Session, fake_clock: FakeClock, published: list[Call]
) -> None:
    ming = make_student(db_session)
    l1 = make_leave(db_session, ming, start_date=date(2026, 9, 1), end_date=date(2026, 9, 3))
    l2 = make_leave(db_session, ming, start_date=date(2026, 9, 5))
    for d in (date(2026, 9, 1), date(2026, 9, 2)):
        make_attendance(db_session, ming, service_date=d, status="leave", leave=l1)
    make_attendance(db_session, ming, service_date=date(2026, 9, 3), status="present")
    make_attendance(db_session, ming, service_date=date(2026, 9, 5), status="leave", leave=l2)
    # 別的學生同日的請假列不動
    hua = make_student(db_session, name="陳小華")
    hua_leave = make_leave(db_session, hua, start_date=date(2026, 9, 1))
    make_attendance(db_session, hua, service_date=date(2026, 9, 1), status="leave", leave=hua_leave)

    reverted = revert_attendance_for_leave(db_session, l1, clock=fake_clock)

    assert reverted == [date(2026, 9, 1), date(2026, 9, 2)]
    rows = _rows(db_session, ming.id)
    for d in reverted:
        assert (rows[d].status, rows[d].leave_id) == ("expected", None)
    assert (rows[date(2026, 9, 5)].status, rows[date(2026, 9, 5)].leave_id) == ("leave", l2.id)
    assert rows[date(2026, 9, 3)].status == "present"
    assert _rows(db_session, hua.id)[date(2026, 9, 1)].status == "leave"

    db_session.commit()
    assert published == [
        (
            [admin_topic_channel("attendance")],
            {
                "type": "attendance.bulk_updated",
                "data": {"student_id": str(ming.id), "dates": ["2026-09-01", "2026-09-02"]},
                "sent_at": "2026-09-01T01:00:00Z",
            },
        )
    ]


def test_revert_leave_attendance_none(
    db_session: Session, fake_clock: FakeClock, published: list[Call]
) -> None:
    leave = make_leave(db_session, make_student(db_session), start_date=date(2026, 9, 10))

    assert revert_attendance_for_leave(db_session, leave, clock=fake_clock) == []
    db_session.commit()
    assert published == []


def test_revert_leave_attendance_from_date(db_session: Session, fake_clock: FakeClock) -> None:
    ming = make_student(db_session)
    leave = make_leave(db_session, ming, start_date=date(2026, 9, 7), end_date=date(2026, 9, 11))
    days = [date(2026, 9, d) for d in range(7, 12)]
    for d in days:
        make_attendance(db_session, ming, service_date=d, status="leave", leave=leave)

    reverted = revert_attendance_for_leave(
        db_session, leave, from_date=date(2026, 9, 9), clock=fake_clock
    )

    assert reverted == [date(2026, 9, 9), date(2026, 9, 10), date(2026, 9, 11)]
    rows = _rows(db_session, ming.id)
    assert [rows[d].status for d in days] == ["leave", "leave", "expected", "expected", "expected"]
