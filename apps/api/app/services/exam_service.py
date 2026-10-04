"""成績模組 service（domain_spec M8）。

BACKEND-452：``resolve_exam_roster``。
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.models.exams import Exam
from app.repositories.students import list_active_student_ids


def resolve_exam_roster(session: Session, exam: Exam) -> list[UUID]:
    """依 exam 的 class_id / grade_level 決定「目前」的應考學生（active、未封存），依 student_no。

    兩者皆有 → 交集；已退班但已有成績的學生由呼叫端以成績列補回（BACKEND-459）。
    """
    grade_levels = None if exam.grade_level is None else [exam.grade_level]
    return list_active_student_ids(session, class_id=exam.class_id, grade_levels=grade_levels)
