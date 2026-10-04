"""BACKEND-341：請假 schemas。"""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.leaves import (
    AttachmentUrlOut,
    LeaveCancelIn,
    LeaveCreateIn,
    LeaveListQuery,
    LeaveOut,
    LeaveStudentOut,
    ParentLeaveAttachmentOut,
    ParentLeaveCreateIn,
    ParentLeaveOut,
)

U = uuid4()


def test_leave_schemas_date_order() -> None:
    with pytest.raises(ValidationError, match="結束日不可早於開始日"):
        LeaveCreateIn(
            student_id=U, leave_type="sick", start_date=date(2026, 9, 2), end_date=date(2026, 9, 1)
        )
    one_day = LeaveCreateIn(
        student_id=U, leave_type="sick", start_date=date(2026, 9, 2), end_date=date(2026, 9, 2)
    )
    assert one_day.end_date == one_day.start_date


def test_leave_schemas_span_limit() -> None:
    ok = LeaveCreateIn(
        student_id=U, leave_type="sick", start_date=date(2026, 9, 1), end_date=date(2026, 10, 31)
    )
    assert (ok.end_date - ok.start_date).days == 60
    with pytest.raises(ValidationError):
        LeaveCreateIn(
            student_id=U,
            leave_type="sick",
            start_date=date(2026, 9, 1),
            end_date=date(2026, 11, 1),
        )
    with pytest.raises(ValidationError):
        ParentLeaveCreateIn(
            student_id=U,
            leave_type="sick",
            start_date=date(2026, 9, 1),
            end_date=date(2026, 11, 1),
        )


def test_leave_schemas_reason_blank() -> None:
    kwargs = {"student_id": U, "start_date": date(2026, 9, 1), "end_date": date(2026, 9, 1)}
    assert LeaveCreateIn(leave_type="sick", reason="   ", **kwargs).reason is None
    assert LeaveCreateIn(leave_type="sick", reason=" 發燒 ", **kwargs).reason == "發燒"
    assert LeaveCreateIn(leave_type="sick", **kwargs).reason is None
    with pytest.raises(ValidationError):
        LeaveCreateIn(leave_type="annual", **kwargs)
    with pytest.raises(ValidationError):
        LeaveCreateIn(leave_type="sick", reason="a" * 501, **kwargs)


def test_leave_schemas_query_range() -> None:
    with pytest.raises(ValidationError):
        LeaveListQuery(date_from=date(2026, 9, 5), date_to=date(2026, 9, 1))
    with pytest.raises(ValidationError):
        ParentLeaveCreateIn.model_validate(
            {
                "student_id": str(U),
                "leave_type": "sick",
                "start_date": "2026-09-01",
                "end_date": "2026-09-01",
                "client_request_id": "x",
            }
        )
    q = LeaveListQuery(created_by_type="parent", status="active")
    assert (q.created_by_type, q.status) == ("parent", "active")
    with pytest.raises(ValidationError):
        LeaveListQuery(status="pending")
    with pytest.raises(ValidationError):
        LeaveListQuery(created_by_type="system")


def test_leave_schemas_cancel_scope() -> None:
    assert LeaveCancelIn().scope == "remaining"
    assert LeaveCancelIn(scope="all").scope == "all"
    with pytest.raises(ValidationError):
        LeaveCancelIn(scope="past")
    with pytest.raises(ValidationError):
        LeaveCancelIn.model_validate({"scope": "all", "x": 1})


def test_leave_schemas_out_models() -> None:
    now = datetime(2026, 9, 1, tzinfo=UTC)
    out = LeaveOut(
        id=uuid4(),
        student=LeaveStudentOut(id=U, student_no="S001", name="王小明", class_name=None),
        leave_type="personal",
        leave_type_label="事假",
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 1),
        reason=None,
        status="active",
        created_by_type="staff",
        created_by_name="王老師",
        created_at=now,
        cancelled_at=None,
        cancelled_by_type=None,
        cancelled_by_name=None,
        attachments=[],
    )
    assert out.student.name == "王小明"
    parent_out = ParentLeaveOut(
        id=uuid4(),
        student_id=U,
        leave_type="sick",
        leave_type_label="病假",
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 1),
        reason=None,
        status="active",
        created_by_type="parent",
        created_at=now,
        cancelled_at=None,
        can_cancel=True,
        attachments=[
            ParentLeaveAttachmentOut(
                id=uuid4(), mime_type="image/png", size_bytes=10, created_at=now, url=None
            )
        ],
    )
    assert parent_out.attachments[0].url is None
    assert "created_by_name" not in ParentLeaveOut.model_fields
    assert AttachmentUrlOut(url="https://r2.example/x", expires_in=300).expires_in == 300
