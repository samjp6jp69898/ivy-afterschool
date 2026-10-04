"""BACKEND-181：家長端子女 / 今日狀態卡 schemas（欄位集合固定，不含敏感欄位）。"""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.parent_children import (
    ChildDetailOut,
    ChildGuardianOut,
    ChildSummaryOut,
    ChildTodayOut,
    ParentMeOut,
    TodayAttendanceOut,
    TodayHomeworkOut,
    TodayLeaveOut,
    TodayPickupRequestOut,
)


def test_parent_children_schemas_fields() -> None:
    assert set(ChildDetailOut.model_fields) == {
        "id", "name", "grade_level", "class_name", "school_name", "photo_url", "status",
        "school_class", "enrolled_on", "my_guardian",
    }  # fmt: skip
    assert set(ChildSummaryOut.model_fields) == {
        "id", "name", "grade_level", "class_name", "school_name", "photo_url", "status",
    }  # fmt: skip
    assert set(ChildGuardianOut.model_fields) == {
        "relation", "is_primary", "can_pickup", "receives_notifications",
    }  # fmt: skip
    assert set(ParentMeOut.model_fields) == {
        "id", "display_name", "picture_url", "phone", "children",
    }  # fmt: skip


def test_parent_children_schemas_no_sensitive() -> None:
    forbidden = {
        "id_number",
        "health_note",
        "note",
        "id_number_enc",
        "id_number_hmac",
        "student_no",
    }
    for model in (ChildDetailOut, ChildSummaryOut, ParentMeOut, ChildGuardianOut):
        assert forbidden.isdisjoint(model.model_fields), model.__name__


def test_parent_children_schemas_today_fields() -> None:
    assert set(ChildTodayOut.model_fields) == {
        "student_id", "date", "is_service_day", "attendance", "on_leave", "leave", "homework",
        "pickup_request",
    }  # fmt: skip
    assert set(TodayPickupRequestOut.model_fields) == {
        "id", "status", "expected_arrival_at", "reply_ready_eta", "reply_message", "reply_source",
        "completed_at", "picked_up_by_name", "can_cancel", "can_mark_arrived",
    }  # fmt: skip
    assert set(TodayAttendanceOut.model_fields) == {"status", "check_in_at", "check_out_at"}
    assert set(TodayLeaveOut.model_fields) == {
        "id", "leave_type", "leave_type_label", "start_date", "end_date",
    }  # fmt: skip
    assert set(TodayHomeworkOut.model_fields) == {
        "item_count", "done_count", "overall_status", "ready_eta", "note",
    }  # fmt: skip


def test_parent_children_schemas_today_value_domains() -> None:
    for status in ("expected", "present", "left", "absent", "leave", None):
        assert (
            TodayAttendanceOut(status=status, check_in_at=None, check_out_at=None).status == status
        )
    with pytest.raises(ValidationError):
        TodayAttendanceOut(status="late", check_in_at=None, check_out_at=None)
    with pytest.raises(ValidationError):
        TodayHomeworkOut(item_count=1, done_count=0, overall_status="x", ready_eta=None, note=None)
    with pytest.raises(ValidationError):
        TodayHomeworkOut(
            item_count=1, done_count=0, overall_status="done", ready_eta="25:00", note=None
        )
    assert (
        TodayHomeworkOut(
            item_count=2, done_count=1, overall_status="in_progress", ready_eta="17:30", note=None
        ).ready_eta
        == "17:30"
    )
    with pytest.raises(ValidationError):
        TodayPickupRequestOut(
            id=uuid4(), status="requested", expected_arrival_at="1730", reply_ready_eta=None,
            reply_message=None, reply_source=None, completed_at=None, picked_up_by_name=None,
            can_cancel=True, can_mark_arrived=False,
        )  # fmt: skip


def test_parent_children_schemas_today_composes() -> None:
    today = ChildTodayOut(
        student_id=uuid4(),
        date=date(2026, 9, 1),
        is_service_day=True,
        attendance=TodayAttendanceOut(
            status="present", check_in_at=datetime(2026, 9, 1, 8, tzinfo=UTC), check_out_at=None
        ),
        on_leave=False,
        leave=None,
        homework=TodayHomeworkOut(
            item_count=0, done_count=0, overall_status="not_started", ready_eta=None, note=None
        ),
        pickup_request=None,
    )
    assert today.attendance.status == "present"
    assert today.pickup_request is None
