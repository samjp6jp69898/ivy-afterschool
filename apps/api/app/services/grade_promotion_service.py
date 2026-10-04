"""BACKEND-157：學年升級預覽（GradePromotionService.preview；domain_spec M3：每年 8 月升級，
grade_level +1、六年級轉 withdrawn，後台批次處理、不自動執行）。

參考 ivy ``api/students.py::bulk_graduate_students`` 的批次轉態；去掉幼稚園畢業流程與 lifecycle。

- 對象：``archived_at is null`` 且 status ∈ {active, suspended}；withdrawn 不動。
- grade 1~5 → promote（grade_to = grade + 1）；grade 6 → graduate（grade_to = None，執行時轉
  withdrawn）。依 grade、student_no 排序。
- 升級不改 class_id：PromotionItem.class_name 顯示原安親班班級，之後由行政逐一調整。
- ``is_already_promoted``：已有 audit ``student.promote_grade`` / entity_type ``academic_year`` /
  entity_id = 學年度 → preview 仍回結果並附 ``already_promoted=True``（BACKEND-158 執行前以同一
  判斷擋重複執行）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final
from uuid import UUID

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.models.audit import AuditLog
from app.models.students import Student

PROMOTE_GRADE_ACTION: Final = "student.promote_grade"
PROMOTE_GRADE_ENTITY_TYPE: Final = "academic_year"
GRADUATING_GRADE: Final = 6
_PROMOTABLE_STATUSES: Final = ("active", "suspended")


@dataclass(frozen=True)
class PromotionItem:
    id: UUID
    student_no: str
    name: str
    grade_from: int
    grade_to: int | None  # 六年級畢業（轉 withdrawn）為 None
    class_name: str | None


@dataclass(frozen=True)
class PromotionPreview:
    from_academic_year: int
    to_academic_year: int
    promote: list[PromotionItem]
    graduate: list[PromotionItem]
    total: int
    already_promoted: bool


def is_already_promoted(session: Session, from_academic_year: int) -> bool:
    return bool(
        session.execute(
            select(
                exists().where(
                    AuditLog.action == PROMOTE_GRADE_ACTION,
                    AuditLog.entity_type == PROMOTE_GRADE_ENTITY_TYPE,
                    AuditLog.entity_id == str(from_academic_year),
                )
            )
        ).scalar_one()
    )


def _candidates(session: Session) -> list[Student]:
    # Student.class_ 為 lazy='joined'，班名一次帶出
    return list(
        session.execute(
            select(Student)
            .where(Student.archived_at.is_(None), Student.status.in_(_PROMOTABLE_STATUSES))
            .order_by(Student.grade_level, Student.student_no, Student.id)
        ).scalars()
    )


def _item(student: Student) -> PromotionItem:
    graduating = student.grade_level >= GRADUATING_GRADE
    return PromotionItem(
        id=student.id,
        student_no=student.student_no,
        name=student.name,
        grade_from=student.grade_level,
        grade_to=None if graduating else student.grade_level + 1,
        class_name=student.class_.name if student.class_ is not None else None,
    )


def preview(session: Session, *, from_academic_year: int) -> PromotionPreview:
    items = [_item(student) for student in _candidates(session)]
    promote = [item for item in items if item.grade_to is not None]
    graduate = [item for item in items if item.grade_to is None]
    return PromotionPreview(
        from_academic_year=from_academic_year,
        to_academic_year=from_academic_year + 1,
        promote=promote,
        graduate=graduate,
        total=len(promote) + len(graduate),
        already_promoted=is_already_promoted(session, from_academic_year),
    )
