"""BACKEND-181：家長端子女與今日狀態卡 schemas。

家長端不輸出敏感欄位（身分證、健康備註）與內部欄位（備註、員工姓名）；欄位集合由測試固定。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from app.core.settings_registry import TimeHM
from app.models.parents import GuardianRelation
from app.models.students import StudentStatus
from app.schemas.common import OutModel

AttendanceStatus = Literal["expected", "present", "left", "absent", "leave"]


class ChildSummaryOut(OutModel):
    id: UUID
    name: str
    grade_level: int
    class_name: str | None
    school_name: str | None  # short_name 優先
    photo_url: str | None
    status: StudentStatus


class ChildGuardianOut(OutModel):
    """家長自己那筆 guardian 的設定。"""

    relation: GuardianRelation
    is_primary: bool
    can_pickup: bool
    receives_notifications: bool


class ChildDetailOut(ChildSummaryOut):
    school_class: str | None
    enrolled_on: date | None
    my_guardian: ChildGuardianOut


class ParentMeOut(OutModel):
    id: UUID
    display_name: str | None
    picture_url: str | None
    phone: str | None
    children: list[ChildSummaryOut]


class TodayAttendanceOut(OutModel):
    status: AttendanceStatus | None
    check_in_at: datetime | None
    check_out_at: datetime | None


class TodayLeaveOut(OutModel):
    id: UUID
    leave_type: Literal["sick", "personal", "other"]
    leave_type_label: str
    start_date: date
    end_date: date


class TodayHomeworkOut(OutModel):
    item_count: int
    done_count: int
    overall_status: Literal["not_started", "in_progress", "done"]
    ready_eta: TimeHM | None
    note: str | None


class TodayPickupRequestOut(OutModel):
    id: UUID
    status: Literal["pending", "acknowledged", "arrived", "completed", "cancelled", "expired"]
    expected_arrival_at: TimeHM | None
    reply_ready_eta: TimeHM | None
    reply_message: str | None
    reply_source: Literal["auto", "staff"] | None
    completed_at: datetime | None
    # 監護人或代理人姓名；主管強制完成且無對象時為「老師確認交付」
    picked_up_by_name: str | None
    can_cancel: bool
    can_mark_arrived: bool


class ChildTodayOut(OutModel):
    student_id: UUID
    date: date
    is_service_day: bool
    attendance: TodayAttendanceOut
    on_leave: bool
    leave: TodayLeaveOut | None
    homework: TodayHomeworkOut
    pickup_request: TodayPickupRequestOut | None
