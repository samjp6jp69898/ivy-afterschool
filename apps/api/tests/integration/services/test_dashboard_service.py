"""BACKEND-491：dashboard_service.get_today_dashboard（今日儀表板聚合）。

計數涵蓋整個 DB（儀表板沒有篩選），測資只在 db_session 交易內建立、結束 rollback。營業時段讀 seed
預設（週一到週五營業）；每個測試前後清空設定快取。
"""

from collections.abc import Iterator
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.models.attendance import AttendanceStatus
from app.models.reference import ClosedDay
from app.services.dashboard_service import get_today_dashboard
from app.services.settings_service import clear_settings_cache
from tests.support.factories import (
    make_attendance,
    make_class,
    make_homework_progress,
    make_leave,
    make_pickup_request,
    make_student,
)
from tests.support.fake_clock import FakeClock

_TODAY = date(2026, 9, 8)  # 週二
_CLOCK = "2026-09-08T15:00:00+08:00"


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    clear_settings_cache()
    yield
    clear_settings_cache()


@pytest.mark.clock(_CLOCK)
def test_dashboard_attendance_counts(db_session: Session, fake_clock: FakeClock) -> None:
    students = [make_student(db_session, name=f"學生{i}") for i in range(6)]
    make_attendance(db_session, students[0], service_date=_TODAY, status="present")
    make_attendance(db_session, students[1], service_date=_TODAY, status="present")
    make_attendance(db_session, students[2], service_date=_TODAY, status="left")
    make_attendance(db_session, students[3], service_date=_TODAY, status="absent")
    leave = make_leave(db_session, students[4], start_date=_TODAY)
    make_attendance(db_session, students[4], service_date=_TODAY, status="leave", leave=leave)
    # students[5] 無列；別天的列與已退班學生不影響
    make_attendance(db_session, students[5], service_date=date(2026, 9, 7), status="present")
    gone = make_student(db_session, name="張小美")
    gone.status = "withdrawn"
    gone.withdrawn_on = date(2026, 9, 1)
    db_session.flush()

    out = get_today_dashboard(db_session, clock=fake_clock)

    assert (out.date, out.is_service_day) == (_TODAY, True)
    assert out.attendance.model_dump() == {
        "expected_total": 5,
        "arrived": 3,
        "present": 2,
        "left": 1,
        "not_arrived": 1,
        "leave": 1,
        "absent": 1,
    }


@pytest.mark.clock(_CLOCK)
def test_dashboard_attendance_expected_rows_and_missing(
    db_session: Session, fake_clock: FakeClock
) -> None:
    first = make_student(db_session)
    make_student(db_session)
    make_attendance(db_session, first, service_date=_TODAY)

    out = get_today_dashboard(db_session, clock=fake_clock)

    assert (out.attendance.expected_total, out.attendance.not_arrived) == (2, 2)


@pytest.mark.clock(_CLOCK)
def test_dashboard_non_service_day_counts_actual_rows_only(
    db_session: Session, fake_clock: FakeClock
) -> None:
    db_session.add(ClosedDay(date=_TODAY, reason="颱風假"))
    present = make_student(db_session)
    make_student(db_session)  # 無列：非營業日不計入
    make_attendance(db_session, present, service_date=_TODAY, status="present")

    out = get_today_dashboard(db_session, clock=fake_clock)

    assert out.is_service_day is False
    assert out.attendance.model_dump() == {
        "expected_total": 1,
        "arrived": 1,
        "present": 1,
        "left": 0,
        "not_arrived": 0,
        "leave": 0,
        "absent": 0,
    }


@pytest.mark.clock(_CLOCK)
def test_dashboard_pickup_counts(db_session: Session, fake_clock: FakeClock) -> None:
    students = [make_student(db_session) for _ in range(5)]
    make_pickup_request(db_session, students[0], service_date=_TODAY)
    make_pickup_request(
        db_session,
        students[1],
        service_date=_TODAY,
        status="acknowledged",
        reply_source="staff",
        reply_message="17:30 好",
    )
    make_pickup_request(
        db_session, students[2], service_date=_TODAY, status="arrived", reply_source="staff"
    )
    make_pickup_request(db_session, students[3], service_date=_TODAY, status="completed")
    # 別天的請求不計
    make_pickup_request(db_session, students[4], service_date=date(2026, 9, 7))

    out = get_today_dashboard(db_session, clock=fake_clock)

    assert out.pickup.model_dump() == {"open": 3, "needs_reply": 1, "arrived": 1, "completed": 1}


@pytest.mark.clock(_CLOCK)
def test_dashboard_pickup_needs_reply_auto(db_session: Session, fake_clock: FakeClock) -> None:
    """auto 回覆：沒有 ETA 且作業未完成才算待回覆；有 ETA 或作業已完成不算；終態不算。"""
    waiting, done, with_eta, no_progress, cancelled = (make_student(db_session) for _ in range(5))
    for student in (waiting, done, no_progress, cancelled):
        make_pickup_request(
            db_session,
            student,
            service_date=_TODAY,
            status="cancelled" if student is cancelled else "acknowledged",
            reply_source="auto",
        )
    make_pickup_request(
        db_session,
        with_eta,
        service_date=_TODAY,
        status="acknowledged",
        reply_source="auto",
        reply_ready_eta=time(17, 30),
    )
    make_homework_progress(db_session, waiting, service_date=_TODAY, overall_status="in_progress")
    make_homework_progress(db_session, done, service_date=_TODAY, overall_status="done")
    # 別天的作業完成不影響今天
    make_homework_progress(
        db_session, no_progress, service_date=date(2026, 9, 7), overall_status="done"
    )

    out = get_today_dashboard(db_session, clock=fake_clock)

    assert out.pickup.model_dump() == {"open": 4, "needs_reply": 2, "arrived": 0, "completed": 0}


@pytest.mark.clock(_CLOCK)
def test_dashboard_homework_rate(db_session: Session, fake_clock: FakeClock) -> None:
    done, doing, idle, absent = (make_student(db_session) for _ in range(4))
    make_attendance(db_session, done, service_date=_TODAY, status="present")
    make_attendance(db_session, doing, service_date=_TODAY, status="left")
    make_attendance(db_session, idle, service_date=_TODAY, status="present")
    make_attendance(db_session, absent, service_date=_TODAY, status="absent")
    make_homework_progress(db_session, done, service_date=_TODAY, overall_status="done")
    make_homework_progress(db_session, doing, service_date=_TODAY, overall_status="in_progress")
    # 未到班學生的作業不計
    make_homework_progress(db_session, absent, service_date=_TODAY, overall_status="done")

    out = get_today_dashboard(db_session, clock=fake_clock)

    assert out.homework.model_dump() == {
        "total": 3,
        "done": 1,
        "in_progress": 1,
        "not_started": 1,
        "completion_rate": 33.3,
    }


@pytest.mark.clock(_CLOCK)
def test_dashboard_homework_rate_empty(db_session: Session, fake_clock: FakeClock) -> None:
    out = get_today_dashboard(db_session, clock=fake_clock)

    assert out.homework.model_dump() == {
        "total": 0,
        "done": 0,
        "in_progress": 0,
        "not_started": 0,
        "completion_rate": 0.0,
    }


@pytest.mark.clock(_CLOCK)
def test_dashboard_recent_leaves(db_session: Session, fake_clock: FakeClock) -> None:
    class_a = make_class(db_session, name="A班")
    ming = make_student(db_session, name="王小明", class_=class_a)
    hua = make_student(db_session, name="陳小華")
    make_leave(db_session, ming, start_date=date(2026, 9, 1), end_date=date(2026, 9, 2))
    soon = make_leave(
        db_session,
        ming,
        start_date=_TODAY,
        end_date=_TODAY + timedelta(days=1),
        leave_type="personal",
    )
    week = make_leave(db_session, hua, start_date=_TODAY + timedelta(days=7))
    make_leave(db_session, hua, start_date=_TODAY + timedelta(days=8))
    make_leave(db_session, ming, start_date=_TODAY + timedelta(days=3), status="cancelled")

    out = get_today_dashboard(db_session, clock=fake_clock)

    assert [leave.id for leave in out.recent_leaves] == [soon.id, week.id]
    first = out.recent_leaves[0]
    assert (first.student_id, first.student_name, first.class_name) == (ming.id, "王小明", "A班")
    assert (first.leave_type_label, first.start_date, first.end_date) == (
        "事假",
        _TODAY,
        _TODAY + timedelta(days=1),
    )
    assert first.created_by_type == "staff"
    assert out.recent_leaves[1].class_name is None


@pytest.mark.clock(_CLOCK)
def test_dashboard_recent_leaves_limit_and_order(
    db_session: Session, fake_clock: FakeClock
) -> None:
    students = [make_student(db_session) for _ in range(12)]
    leaves = [
        make_leave(db_session, s, start_date=_TODAY + timedelta(days=i % 4))
        for i, s in enumerate(students)
    ]
    for index, leave in enumerate(leaves):
        leave.created_at = datetime(2026, 9, 1, tzinfo=UTC) + timedelta(minutes=index)
    db_session.flush()

    out = get_today_dashboard(db_session, clock=fake_clock)

    expected = sorted(leaves, key=lambda leave: (leave.start_date, leave.created_at))[:10]
    assert [leave.id for leave in out.recent_leaves] == [leave.id for leave in expected]


@pytest.mark.clock(_CLOCK)
def test_dashboard_query_count(db_session: Session, fake_clock: FakeClock) -> None:
    statuses: list[AttendanceStatus | None] = ["present", "left", "absent", "expected", None]
    for index in range(50):
        student = make_student(db_session)
        status = statuses[index % len(statuses)]
        if status is not None:
            make_attendance(db_session, student, service_date=_TODAY, status=status)
        if index % 4 == 0:
            make_homework_progress(db_session, student, service_date=_TODAY, overall_status="done")
        if index % 7 == 0:
            make_pickup_request(db_session, student, service_date=_TODAY)
        if index % 9 == 0:
            make_leave(db_session, student, start_date=_TODAY + timedelta(days=1))
    statements: list[str] = []

    def record(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        statements.append(statement)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", record)
    try:
        out = get_today_dashboard(db_session, clock=fake_clock)
    finally:
        event.remove(engine, "before_cursor_execute", record)

    assert out.attendance.expected_total == 50
    assert out.pickup.open == 8
    assert len(out.recent_leaves) == 6
    assert len(statements) <= 6
