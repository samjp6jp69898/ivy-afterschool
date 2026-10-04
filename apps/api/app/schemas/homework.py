"""BACKEND-371：作業進度模組 schemas。

時間一律 ``HH:MM`` 字串（``TimeHM``，與 settings_registry 相同）。ProgressPutIn 以
``model_fields_set`` 區分 ready_eta / note「未給」與「給 null（清除）」。
"""

from __future__ import annotations

import datetime as dt
from typing import Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from app.core.settings_registry import TimeHM
from app.schemas.attendance import AttendanceStatus
from app.schemas.common import OutModel, RequestModel, SortOrder, UpdateModel

HomeworkItemStatus = Literal["todo", "doing", "correcting", "done"]
OverallStatus = Literal["not_started", "in_progress", "done"]

Title = Field(min_length=1, max_length=100)


class HomeworkItemCreateIn(RequestModel):
    student_id: UUID
    service_date: dt.date | None = None  # service 以今天補
    subject_id: UUID | None = None
    title: str = Title
    status: HomeworkItemStatus = "todo"
    sort_order: SortOrder = 0


class HomeworkBatchCreateIn(RequestModel):
    class_id: UUID
    service_date: dt.date | None = None
    subject_id: UUID | None = None
    title: str = Title
    # 不給 = 整班；給值時必須屬於該班（service 驗證）
    student_ids: list[UUID] | None = Field(default=None, min_length=1, max_length=100)

    @model_validator(mode="after")
    def _unique_students(self) -> Self:
        ids = self.student_ids
        if ids is not None and len(set(ids)) != len(ids):
            raise ValueError("student_ids 不可重複")
        return self


class HomeworkItemUpdateIn(UpdateModel):
    nullable_fields = frozenset({"subject_id"})

    title: str | None = Field(default=None, min_length=1, max_length=100)
    subject_id: UUID | None = None
    status: HomeworkItemStatus | None = None
    sort_order: SortOrder | None = None


class ProgressPutIn(RequestModel):
    """service_date 之外至少要給一個欄位。

    overall：done = 員工直接標整體完成；auto = 改回由項目推導（不可為 null）。
    ready_eta / note 給 null = 清除。
    """

    service_date: dt.date | None = None
    overall: Literal["done", "auto"] | None = None
    ready_eta: TimeHM | None = None
    note: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _require_a_change(self) -> Self:
        changes = self.model_fields_set - {"service_date"}
        if not changes:
            raise ValueError("除 service_date 外至少要給一個欄位")
        if "overall" in changes and self.overall is None:
            raise ValueError("overall 不可為 null")
        return self


class HomeworkItemOut(OutModel):
    id: UUID
    student_id: UUID
    service_date: dt.date
    subject_id: UUID | None
    subject_name: str | None
    title: str
    status: HomeworkItemStatus
    sort_order: int
    updated_at: dt.datetime


class ProgressOut(OutModel):
    student_id: UUID
    service_date: dt.date
    overall_status: OverallStatus
    ready_eta: TimeHM | None
    note: str | None
    eta_updated_at: dt.datetime | None
    eta_updated_by_name: str | None


class HomeworkMutationOut(OutModel):
    item: HomeworkItemOut | None  # 刪除時為 null
    progress: ProgressOut


class HomeworkBatchOut(OutModel):
    created: int
    items: list[HomeworkItemOut]


class BoardQuery(RequestModel):
    date: dt.date | None = None
    class_id: UUID | None = None


class BoardStudentOut(OutModel):
    student_id: UUID
    student_no: str
    name: str
    class_id: UUID | None
    class_name: str | None
    attendance_status: AttendanceStatus | None
    items: list[HomeworkItemOut]
    progress: ProgressOut


class BoardSummaryOut(OutModel):
    total: int
    done: int
    in_progress: int
    not_started: int


class BoardWindowOut(OutModel):
    past_days: int
    future_days: int


class BoardOut(OutModel):
    date: dt.date
    window: BoardWindowOut
    summary: BoardSummaryOut
    students: list[BoardStudentOut]


class ParentHomeworkItemOut(OutModel):
    title: str
    subject_name: str | None
    status: HomeworkItemStatus


class ParentHomeworkOut(OutModel):
    student_id: UUID
    date: dt.date
    items: list[ParentHomeworkItemOut]
    overall_status: OverallStatus
    ready_eta: TimeHM | None
    note: str | None
    updated_at: dt.datetime | None  # items 與 progress 中最晚的 updated_at
