"""BACKEND-341：請假 schemas（跨度上限對齊 DB-018 ck_student_leaves_range）。"""

from __future__ import annotations

import datetime as dt
from typing import Literal, Self
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.schemas.common import OutModel, RequestModel

LeaveType = Literal["sick", "personal", "other"]
LeaveStatus = Literal["active", "cancelled"]
CreatedByType = Literal["parent", "staff"]

MAX_SPAN_DAYS = 60  # end - start <= 60，單筆最長 61 天


class _LeaveCreateBase(RequestModel):
    student_id: UUID
    leave_type: LeaveType
    start_date: dt.date
    end_date: dt.date
    reason: str | None = Field(default=None, max_length=500)

    @field_validator("reason")
    @classmethod
    def _blank_reason_to_none(cls, value: str | None) -> str | None:
        return value or None

    @model_validator(mode="after")
    def _check_range(self) -> Self:
        if self.end_date < self.start_date:
            raise ValueError("結束日不可早於開始日")
        if (self.end_date - self.start_date).days > MAX_SPAN_DAYS:
            raise ValueError("單筆請假最長 61 天")
        return self


class LeaveCreateIn(_LeaveCreateBase):
    """後台代登記。"""


class ParentLeaveCreateIn(_LeaveCreateBase):
    """家長端申請（與後台分開宣告以便 OpenAPI 區分）。"""


class LeaveListQuery(RequestModel):
    student_id: UUID | None = None
    class_id: UUID | None = None
    status: LeaveStatus | None = None
    leave_type: LeaveType | None = None
    created_by_type: CreatedByType | None = None
    date_from: dt.date | None = None  # 與請假區間有交集即符合
    date_to: dt.date | None = None

    @model_validator(mode="after")
    def _check_range(self) -> Self:
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("開始日不可晚於結束日")
        return self


class LeaveStudentOut(OutModel):
    id: UUID
    student_no: str
    name: str
    class_name: str | None


class LeaveAttachmentOut(OutModel):
    id: UUID
    mime_type: str
    size_bytes: int
    created_at: dt.datetime


class ParentLeaveAttachmentOut(LeaveAttachmentOut):
    url: str | None  # 短效簽名 URL，取得失敗時 null


class LeaveOut(OutModel):
    id: UUID
    student: LeaveStudentOut
    leave_type: LeaveType
    leave_type_label: str
    start_date: dt.date
    end_date: dt.date
    reason: str | None
    status: LeaveStatus
    created_by_type: CreatedByType
    created_by_name: str | None
    created_at: dt.datetime
    cancelled_at: dt.datetime | None
    cancelled_by_type: CreatedByType | None
    cancelled_by_name: str | None
    attachments: list[LeaveAttachmentOut]


class ParentLeaveOut(OutModel):
    id: UUID
    student_id: UUID
    leave_type: LeaveType
    leave_type_label: str
    start_date: dt.date
    end_date: dt.date
    reason: str | None
    status: LeaveStatus
    created_by_type: CreatedByType
    created_at: dt.datetime
    cancelled_at: dt.datetime | None
    can_cancel: bool  # active 且 end_date >= 今天
    attachments: list[ParentLeaveAttachmentOut]


class AttachmentUrlOut(OutModel):
    url: str
    expires_in: int  # 秒


class LeaveCancelIn(RequestModel):
    """後台取消 body（可省略）。remaining = 取消今天起尚未到的日子；all = 整筆取消。"""

    scope: Literal["remaining", "all"] = "remaining"
