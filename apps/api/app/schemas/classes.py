"""BACKEND-134：班級 schemas（欄位限制對齊 DB-012 CHECK）。"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Self
from uuid import UUID

from pydantic import AfterValidator, Field, model_validator

from app.models.classes import ClassStaffRole
from app.schemas.common import OutModel, RequestModel, UpdateModel


def _unique_sorted(levels: list[int]) -> list[int]:
    if len(set(levels)) != len(levels):
        raise ValueError("年級不可重複")
    return sorted(levels)


GradeLevels = Annotated[
    list[Annotated[int, Field(ge=1, le=6)]],
    Field(min_length=1, max_length=6),
    AfterValidator(_unique_sorted),
]


class ClassCreateIn(RequestModel):
    name: str = Field(min_length=1, max_length=30)
    grade_levels: GradeLevels
    academic_year: int = Field(ge=100, le=200)  # 民國學年度
    sort_order: int = Field(default=0, ge=0)


class ClassUpdateIn(UpdateModel):
    name: str | None = Field(default=None, min_length=1, max_length=30)
    grade_levels: GradeLevels | None = None
    academic_year: int | None = Field(default=None, ge=100, le=200)
    sort_order: int | None = Field(default=None, ge=0)


class ClassListQuery(RequestModel):
    academic_year: int | None = None
    include_archived: bool = False
    mine: bool = False  # 只列目前員工在 class_staff 中的班


class ClassStaffItemIn(RequestModel):
    staff_user_id: UUID
    role: ClassStaffRole


class ClassStaffPutIn(RequestModel):
    items: list[ClassStaffItemIn] = Field(max_length=20)

    @model_validator(mode="after")
    def _unique_staff(self) -> Self:
        ids = [item.staff_user_id for item in self.items]
        if len(set(ids)) != len(ids):
            raise ValueError("同一位員工不可重複指派")
        return self


class ClassBriefOut(OutModel):
    id: UUID
    name: str


class ClassStaffOut(OutModel):
    staff_user_id: UUID
    display_name: str
    role: ClassStaffRole


class ClassOut(OutModel):
    id: UUID
    name: str
    grade_levels: list[int]
    academic_year: int
    sort_order: int
    archived_at: datetime | None
    student_count: int  # 未封存且 status active 的學生數
    staff: list[ClassStaffOut]
