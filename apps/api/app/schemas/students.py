"""BACKEND-148：學生模組 schemas（欄位限制對齊 DB-014 CHECK）。

敏感欄位（id_number、health_note）只在持 ``students:sensitive`` 時以明文放進 ``sensitive``；
其餘情況詳情只回 ``has_id_number`` / ``has_health_note``。班級在 JSON 以 ``class`` 為 key。
"""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import ConfigDict, Field

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
