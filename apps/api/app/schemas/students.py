"""BACKEND-148：學生模組 schemas（欄位限制對齊 DB-014 CHECK）。

敏感欄位（id_number、health_note）只在持 ``students:sensitive`` 時以明文放進 ``sensitive``；
其餘情況詳情只回 ``has_id_number`` / ``has_health_note``。班級在 JSON 以 ``class`` 為 key。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Self
from uuid import UUID

from pydantic import ConfigDict, Field, model_validator

from app.models.students import Gender, StudentStatus
from app.schemas.classes import ClassBriefOut
from app.schemas.common import OutModel, RequestModel, UpdateModel
from app.schemas.guardians import GuardianOut

STUDENT_NO_PATTERN = r"^[A-Za-z0-9-]{1,20}$"

ClassBrief = ClassBriefOut


class StudentCreateIn(RequestModel):
    student_no: str = Field(pattern=STUDENT_NO_PATTERN)
    name: str = Field(min_length=1, max_length=50)
    gender: Gender | None = None
    birthday: date | None = None
    grade_level: int = Field(ge=1, le=6)
    school_id: UUID | None = None
    school_class: str | None = Field(default=None, max_length=20)
    class_id: UUID | None = None
    status: StudentStatus = "active"
    enrolled_on: date | None = None
    withdrawn_on: date | None = None
    note: str | None = Field(default=None, max_length=500)
    id_number: str | None = Field(default=None, max_length=20)
    health_note: str | None = Field(default=None, max_length=1000)


class StudentUpdateIn(UpdateModel):
    """全 optional；以 ``model_fields_set`` 區分「未給」與「給 null（清除）」。"""

    nullable_fields = frozenset(
        {
            "gender",
            "birthday",
            "school_id",
            "school_class",
            "class_id",
            "enrolled_on",
            "withdrawn_on",
            "note",
            "id_number",
            "health_note",
        }
    )

    student_no: str | None = Field(default=None, pattern=STUDENT_NO_PATTERN)
    name: str | None = Field(default=None, min_length=1, max_length=50)
    gender: Gender | None = None
    birthday: date | None = None
    grade_level: int | None = Field(default=None, ge=1, le=6)
    school_id: UUID | None = None
    school_class: str | None = Field(default=None, max_length=20)
    class_id: UUID | None = None
    status: StudentStatus | None = None
    enrolled_on: date | None = None
    withdrawn_on: date | None = None
    note: str | None = Field(default=None, max_length=500)
    id_number: str | None = Field(default=None, max_length=20)
    health_note: str | None = Field(default=None, max_length=1000)


class StudentListQuery(RequestModel):
    q: str | None = Field(default=None, max_length=50)
    class_id: UUID | None = None
    grade_level: int | None = Field(default=None, ge=1, le=6)
    status: StudentStatus | None = None
    school_id: UUID | None = None
    include_archived: bool = False


class SchoolBrief(OutModel):
    id: UUID
    name: str
    short_name: str | None


class StudentListItemOut(OutModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    student_no: str
    name: str
    gender: Gender | None
    grade_level: int
    school: SchoolBrief | None
    school_class: str | None
    class_: ClassBrief | None = Field(alias="class")
    status: StudentStatus
    archived_at: datetime | None


class StudentSensitiveOut(OutModel):
    id_number: str | None
    health_note: str | None


class StudentDetailOut(StudentListItemOut):
    birthday: date | None
    enrolled_on: date | None
    withdrawn_on: date | None
    note: str | None
    photo_url: str | None  # 短效簽名 URL
    has_id_number: bool
    has_health_note: bool
    sensitive: StudentSensitiveOut | None  # 只有 students:sensitive 時才有值
    guardians: list[GuardianOut]


class PhotoUploadOut(OutModel):
    photo_url: str


class StudentPurgeIn(RequestModel):
    confirm_student_no: str = Field(min_length=1, max_length=20)  # service 比對目前學號


class StudentPurgeOut(OutModel):
    student_id: UUID
    purged_at: datetime
    anonymized_student_no: str


# --- BACKEND-165：學年升級 ------------------------------------------------------------------------


class PromoteGradeIn(RequestModel):
    """``dry_run=true`` 只預覽；執行（``dry_run=false``）必須帶預覽當時的 ``expected_total``。"""

    from_academic_year: int = Field(ge=100, le=200)
    dry_run: bool = True
    expected_total: int | None = Field(default=None, ge=0, le=100_000)
    withdrawn_on: date | None = None

    @model_validator(mode="after")
    def _require_expected_total_for_execute(self) -> Self:
        if not self.dry_run and self.expected_total is None:
            raise ValueError("執行升級必須提供 expected_total（預覽當時的人數）")
        return self


class PromotionItemOut(OutModel):
    id: UUID
    student_no: str
    name: str
    grade_from: int
    grade_to: int | None  # 六年級畢業（轉 withdrawn）為 None
    class_name: str | None


class PromotionPreviewOut(OutModel):
    from_academic_year: int
    to_academic_year: int
    promote: list[PromotionItemOut]
    graduate: list[PromotionItemOut]
    total: int
    already_promoted: bool


class PromotionResultOut(OutModel):
    promoted: int
    graduated: int


# --- BACKEND-166：Excel 匯入 ----------------------------------------------------------------------


class ImportRowOut(OutModel):
    """只回原始儲存格字串（敏感欄位已遮罩）與錯誤；不含正規化資料（可能帶身分證明文）。"""

    row_number: int
    display: dict[str, str]
    errors: list[str]


class ImportPreviewOut(OutModel):
    rows: list[ImportRowOut]
    total: int
    valid: int
    invalid: int


class ImportResultOut(OutModel):
    created: int
    student_ids: list[UUID]
