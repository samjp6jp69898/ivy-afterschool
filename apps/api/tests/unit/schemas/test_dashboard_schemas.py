"""BACKEND-490：儀表板 schemas。"""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.dashboard import (
    AttendanceCountsOut,
    DashboardTodayOut,
    HomeworkCountsOut,
    PickupCountsOut,
    RecentLeaveOut,
)


def test_dashboard_schemas_rate_rounding() -> None:
    h = HomeworkCountsOut(total=3, done=1, in_progress=1, not_started=1, completion_rate=33.333)
    assert h.completion_rate == 33.3
    assert HomeworkCountsOut(
        total=3, done=2, in_progress=1, not_started=0, completion_rate=66.667
    ).completion_rate == 66.7


def test_dashboard_schemas_rate_bounds() -> None:
    zero = HomeworkCountsOut(total=0, done=0, in_progress=0, not_started=0, completion_rate=0.0)
    assert zero.completion_rate == 0.0
    for bad in (-0.1, 100.1):
        with pytest.raises(ValidationError):
            HomeworkCountsOut(
                total=1, done=0, in_progress=0, not_started=1, completion_rate=bad
            )


def _today(recent: list[RecentLeaveOut]) -> DashboardTodayOut:
    return DashboardTodayOut(
        date=date(2026, 9, 1),
        is_service_day=True,
        attendance=AttendanceCountsOut(
            expected_total=10, arrived=4, present=3, left=1, not_arrived=6, leave=2, absent=1
        ),
        pickup=PickupCountsOut(open=2, needs_reply=1, arrived=1, completed=3),
        homework=HomeworkCountsOut(
            total=4, done=1, in_progress=2, not_started=1, completion_rate=25.0
        ),
        recent_leaves=recent,
    )


def test_dashboard_schemas_shape() -> None:
    assert set(DashboardTodayOut.model_json_schema()["properties"]) == {
        "date", "is_service_day", "attendance", "pickup", "homework", "recent_leaves",
    }  # fmt: skip
    assert set(AttendanceCountsOut.model_fields) == {
        "expected_total", "arrived", "present", "left", "not_arrived", "leave", "absent",
    }  # fmt: skip
    assert set(PickupCountsOut.model_fields) == {"open", "needs_reply", "arrived", "completed"}


def test_dashboard_schemas_recent_leaves_max_10() -> None:
    leave = RecentLeaveOut(
        id=uuid4(),
        student_id=uuid4(),
        student_name="王小明",
        class_name=None,
        leave_type_label="病假",
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 2),
        created_by_type="parent",
        created_at=datetime(2026, 9, 1, tzinfo=UTC),
    )
    assert len(_today([leave] * 10).recent_leaves) == 10
    with pytest.raises(ValidationError):
        _today([leave] * 11)
