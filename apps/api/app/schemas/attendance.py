"""BACKEND-301：出勤 schemas。

時間欄位一律輸出 ISO8601 UTC（前端自行轉台北時間）。簽到 / 簽退 / 標缺席不接受 client 指定時間，
一律以伺服器時間；補登或更正走改判（AttendanceAmendIn）。
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Any, Final, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from app.schemas.common import OutModel, RequestModel

AttendanceStatus = Literal["expected", "present", "left", "absent", "leave"]

STATUS_LABELS: Final[dict[str, str]] = {
    "expected": "預計到班",
    "present": "已到班",
    "left": "已離班",
    "absent": "缺席",
    "leave": "請假",
}

Note = Annotated[str, Field(max_length=200)]
MONTH_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"


class LeaveBriefOut(OutModel):
    id: UUID
    leave_type: str
    leave_type_label: str
    start_date: dt.date
    end_date: dt.date


class AttendanceRowOut(OutModel):
    # 營業日尚未建立紀錄的學生以虛擬列呈現，id 為 null
    id: UUID | None
    student_id: UUID
    student_no: str
    student_name: str
    grade_level: int
    class_id: UUID | None
    class_name: str | None
    service_date: dt.date
    status: AttendanceStatus
    check_in_at: dt.datetime | None
    check_in_source: Literal["manual", "nfc"] | None
    check_out_at: dt.datetime | None
    check_out_source: Literal["manual", "pickup", "nfc"] | None
    leave: LeaveBriefOut | None
    note: str | None
    updated_at: dt.datetime | None


class DailyAttendanceQuery(RequestModel):
    date: dt.date | None = None  # service 以 clock.today() 補
    class_id: UUID | None = None
    status: AttendanceStatus | None = None


class DailySummaryOut(OutModel):
    total: int
    expected: int
    present: int
    left: int
    absent: int
    leave: int


class DailyAttendanceOut(OutModel):
    date: dt.date
    is_service_day: bool
    summary: DailySummaryOut
    items: list[AttendanceRowOut]


class CheckInIn(RequestModel):
    note: Note | None = None


class CheckOutIn(RequestModel):
    note: Note | None = None


class MarkAbsentIn(RequestModel):
    note: Note | None = None


class BatchCheckInIn(RequestModel):
    student_ids: list[UUID] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def _unique_ids(self) -> Self:
        if len(set(self.student_ids)) != len(self.student_ids):
            raise ValueError("student_ids 不可重複")
        return self


class BatchSkipOut(OutModel):
    student_id: UUID
    code: str
    message: str


class BatchCheckInOut(OutModel):
    succeeded: list[AttendanceRowOut]
    skipped: list[BatchSkipOut]


class AttendanceAmendIn(RequestModel):
    """改判。除 reason 外至少給一個欄位；以 model_fields_set 區分「未給」與「給 null」。"""

    status: Literal["expected", "present", "left", "absent"] | None = None
    check_in_at: AwareDatetime | None = None
    check_out_at: AwareDatetime | None = None
    note: Note | None = None
    reason: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def _require_a_change(self) -> Self:
        changed = self.model_fields_set - {"reason"}
        if self.status is None:
            changed -= {"status"}
        if not changed:
            raise ValueError("除 reason 外至少要修改一個欄位")
        return self


class MonthlyAttendanceQuery(RequestModel):
    month: str = Field(pattern=MONTH_PATTERN)
    class_id: UUID | None = None


class MonthDayOut(OutModel):
    date: dt.date
    weekday: int = Field(ge=0, le=6)  # 0 = 週一
    is_service_day: bool


class MonthlyStatsOut(OutModel):
    service_days: int
    attended: int  # present + left
    absent: int
    leave: int
    unrecorded: int


class MonthlyStudentRowOut(OutModel):
    student_id: UUID
    student_no: str
    name: str
    class_name: str | None
    statuses: list[AttendanceStatus | None]  # 與 days 等長、同順序
    stats: MonthlyStatsOut


class MonthlyAttendanceOut(OutModel):
    month: str
    class_id: UUID | None
    class_name: str | None
    days: list[MonthDayOut]
    students: list[MonthlyStudentRowOut]
    totals: MonthlyStatsOut


class ParentAttendanceDayOut(OutModel):
    date: dt.date
    is_service_day: bool
    status: AttendanceStatus | None
    check_in_at: dt.datetime | None
    check_out_at: dt.datetime | None
    leave_type: str | None


class ParentMonthlyAttendanceOut(OutModel):
    student_id: UUID
    month: str
    days: list[ParentAttendanceDayOut]
    stats: MonthlyStatsOut


class ParentAttendanceEventOut(OutModel):
    """推給家長 ws 的裁切資料（不含備註、姓名等內部欄位）。"""

    student_id: UUID
    service_date: dt.date
    status: AttendanceStatus
    check_in_at: dt.datetime | None
    check_out_at: dt.datetime | None


def to_parent_attendance_event(row: Any) -> dict[str, Any]:
    """出勤列 → 家長 ws 事件資料（JSON 可序列化）；出勤 service 共用。"""
    return ParentAttendanceEventOut.model_validate(row).model_dump(mode="json")
