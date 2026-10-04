"""BACKEND-451：考試成績 schemas。

分數 ``Score`` 為 0~1000、最多 2 位小數；Out 的 Decimal 一律序列化為 JSON number（float），
前端不需處理字串。範圍必填（grade_level 與 class_id 至少一個）對齊 DB-026 ck_exams_scope。
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import ConfigDict, Field, PlainSerializer, model_validator

from app.schemas.classes import ClassBriefOut
from app.schemas.common import OutModel, RequestModel, UpdateModel

Score = Annotated[Decimal, Field(ge=0, le=1000, max_digits=6, decimal_places=2)]
FullScore = Annotated[Decimal, Field(gt=0, le=1000, max_digits=6, decimal_places=2)]
JsonNumber = Annotated[Decimal, PlainSerializer(float, return_type=float)]

ExamStatus = Literal["draft", "published"]
GradeLevel = Annotated[int, Field(ge=1, le=6)]


class ExamCreateIn(RequestModel):
    name: str = Field(min_length=1, max_length=50)
    exam_type_id: UUID
    exam_date: dt.date
    grade_level: GradeLevel | None = None
    class_id: UUID | None = None
    note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _scope_required(self) -> Self:
        if self.grade_level is None and self.class_id is None:
            raise ValueError("grade_level 與 class_id 至少要給一個")
        return self


class ExamUpdateIn(UpdateModel):
    """合併後的範圍檢查在 service。"""

    nullable_fields = frozenset({"grade_level", "class_id", "note"})

    name: str | None = Field(default=None, min_length=1, max_length=50)
    exam_type_id: UUID | None = None
    exam_date: dt.date | None = None
    grade_level: GradeLevel | None = None
    class_id: UUID | None = None
    note: str | None = Field(default=None, max_length=500)


class ExamListQuery(RequestModel):
    q: str | None = Field(default=None, max_length=50)
    status: ExamStatus | None = None
    exam_type_id: UUID | None = None
    class_id: UUID | None = None
    grade_level: GradeLevel | None = None
    date_from: dt.date | None = None
    date_to: dt.date | None = None


class ExamSubjectIn(RequestModel):
    subject_id: UUID
    full_score: FullScore = Decimal(100)
    sort_order: int = Field(default=0, ge=0)


class ExamSubjectsPutIn(RequestModel):
    items: list[ExamSubjectIn] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def _unique_subjects(self) -> Self:
        ids = [item.subject_id for item in self.items]
        if len(set(ids)) != len(ids):
            raise ValueError("科目不可重複")
        return self


class ExamSubjectOut(OutModel):
    subject_id: UUID
    subject_name: str
    full_score: JsonNumber
    sort_order: int


class ExamTypeBriefOut(OutModel):
    id: UUID
    name: str


class ExamOut(OutModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    name: str
    exam_type: ExamTypeBriefOut
    exam_date: dt.date
    grade_level: int | None
    class_: ClassBriefOut | None = Field(alias="class")
    status: ExamStatus
    published_at: dt.datetime | None
    published_by_name: str | None
    note: str | None
    subjects: list[ExamSubjectOut]
    roster_count: int
    created_at: dt.datetime


class ScoreCellIn(RequestModel):
    student_id: UUID
    subject_id: UUID
    score: Score | None = None
    is_absent: bool = False
    note: str | None = Field(default=None, max_length=200)


class ScoresPutIn(RequestModel):
    cells: list[ScoreCellIn] = Field(min_length=1, max_length=2000)
    notify_parents: bool = False  # 只在已發布考試修改時使用


class ScoreCellOut(OutModel):
    student_id: UUID
    subject_id: UUID
    score: JsonNumber | None
    is_absent: bool
    note: str | None
    updated_at: dt.datetime


class GridStudentOut(OutModel):
    id: UUID
    student_no: str
    name: str
    class_name: str | None
    in_roster: bool


class ScoreGridOut(OutModel):
    exam: ExamOut
    students: list[GridStudentOut]
    subjects: list[ExamSubjectOut]
    cells: list[ScoreCellOut]


class ScoresPutOut(OutModel):
    written: int
    changed: int
    renotified_students: int


class SubjectSummaryOut(OutModel):
    subject_id: UUID
    subject_name: str
    full_score: JsonNumber
    scored_count: int
    absent_count: int
    missing_count: int
    average: JsonNumber | None  # 2 位小數
    max: JsonNumber | None
    min: JsonNumber | None


class ExamSummaryOut(OutModel):
    exam_id: UUID
    roster_count: int
    subjects: list[SubjectSummaryOut]


class HistoryScoreOut(OutModel):
    subject_id: UUID
    subject_name: str
    full_score: JsonNumber
    score: JsonNumber | None
    is_absent: bool


class StudentExamHistoryItemOut(OutModel):
    exam_id: UUID
    exam_name: str
    exam_type_name: str
    exam_date: dt.date
    status: ExamStatus
    scores: list[HistoryScoreOut]


class ParentExamListItemOut(OutModel):
    exam_id: UUID
    name: str
    exam_type_name: str
    exam_date: dt.date
    published_at: dt.datetime
    subject_count: int


class ParentExamSubjectScoreOut(OutModel):
    subject_name: str
    full_score: JsonNumber
    score: JsonNumber | None
    is_absent: bool
    note: str | None


class ParentExamDetailOut(OutModel):
    exam_id: UUID
    name: str
    exam_type_name: str
    exam_date: dt.date
    note: str | None
    subjects: list[ParentExamSubjectScoreOut]
