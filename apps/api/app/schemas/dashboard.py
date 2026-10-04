"""BACKEND-490：儀表板「今日」schemas（domain_spec M11）。"""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Any
from uuid import UUID

from pydantic import BeforeValidator, Field

from app.schemas.common import OutModel


def _round_one_decimal(value: Any) -> Any:
    return round(float(value), 1) if isinstance(value, int | float) else value


# 0~100，1 位小數（先四捨五入再檢查範圍）
Rate = Annotated[float, BeforeValidator(_round_one_decimal), Field(ge=0, le=100)]


class AttendanceCountsOut(OutModel):
    expected_total: int  # 應到 = 非請假的出勤列 + 營業日尚無出勤列的 active 學生
    arrived: int  # present + left
    present: int
    left: int
    not_arrived: int  # expected + 無列
    leave: int
    absent: int


class PickupCountsOut(OutModel):
    open: int  # 非終態
    needs_reply: int
    arrived: int  # 等待交付
    completed: int


class HomeworkCountsOut(OutModel):
    total: int  # 今日已到班學生數（present + left）
    done: int
    in_progress: int
    not_started: int
    completion_rate: Rate  # total 為 0 時 0.0


class RecentLeaveOut(OutModel):
    id: UUID
    student_id: UUID
    student_name: str
    class_name: str | None
    leave_type_label: str
    start_date: dt.date
    end_date: dt.date
    created_by_type: str
    created_at: dt.datetime


class DashboardTodayOut(OutModel):
    date: dt.date
    is_service_day: bool
    attendance: AttendanceCountsOut
    pickup: PickupCountsOut
    homework: HomeworkCountsOut
    recent_leaves: list[RecentLeaveOut] = Field(max_length=10)
