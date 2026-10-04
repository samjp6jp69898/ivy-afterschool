"""BACKEND-182：家長端子女清單。

範圍與順序沿用 BACKEND-179 ``get_parent_student_ids``（每次查 DB，解除綁定立即生效）。
照片以短效簽名 URL 回傳；簽發失敗（StorageError）只讓該筆 photo_url 為 None，不讓整份清單失敗。
"""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.storage import Storage, StorageError
from app.models.classes import SchoolClass
from app.models.parents import Guardian
from app.models.reference import School
from app.models.students import Student
from app.schemas.parent_children import ChildDetailOut, ChildGuardianOut, ChildSummaryOut
from app.services.parent_scope import assert_parent_owns_student, get_parent_student_ids

logger = logging.getLogger(__name__)


def list_children(session: Session, parent_id: UUID, *, storage: Storage) -> list[ChildSummaryOut]:
    student_ids = get_parent_student_ids(session, parent_id)
    if not student_ids:
        return []

    rows = session.execute(
        select(
            Student.id,
            Student.name,
            Student.grade_level,
            Student.status,
            Student.photo_path,
            SchoolClass.name,
            School.short_name,
            School.name,
        )
        .outerjoin(SchoolClass, SchoolClass.id == Student.class_id)
        .outerjoin(School, School.id == Student.school_id)
        .where(Student.id.in_(student_ids))
    ).all()
    by_id = {row[0]: row for row in rows}

    children: list[ChildSummaryOut] = []
    for student_id in student_ids:
        _, name, grade_level, status, photo_path, class_name, school_short, school_name = by_id[
            student_id
        ]
        children.append(
            ChildSummaryOut(
                id=student_id,
                name=name,
                grade_level=grade_level,
                class_name=class_name,
                school_name=school_short or school_name,
                photo_url=_photo_url(storage, student_id, photo_path),
                status=status,
            )
        )
    return children


def _photo_url(storage: Storage, student_id: UUID, photo_path: str | None) -> str | None:
    if photo_path is None:
        return None
    try:
        return storage.create_signed_url("student-photos", photo_path)
    except StorageError:
        logger.warning("學生照片簽名 URL 產生失敗 student_id=%s", student_id)
        return None


def get_child(
    session: Session, parent_id: UUID, student_id: UUID, *, storage: Storage
) -> ChildDetailOut:
    """自己的小孩詳情（IDOR：不屬於自己 → 404）；不含健康、過敏、備註等欄位。"""
    student = assert_parent_owns_student(session, parent_id, student_id)
    summary = next(
        c for c in list_children(session, parent_id, storage=storage) if c.id == student_id
    )
    mine = session.execute(
        select(Guardian)
        .where(
            Guardian.student_id == student_id,
            Guardian.parent_account_id == parent_id,
            Guardian.archived_at.is_(None),
        )
        .limit(1)
    ).scalar_one()
    return ChildDetailOut(
        **summary.model_dump(),
        school_class=student.school_class,
        enrolled_on=student.enrolled_on,
        my_guardian=ChildGuardianOut(
            relation=mine.relation,
            is_primary=mine.is_primary,
            can_pickup=mine.can_pickup,
            receives_notifications=mine.receives_notifications,
        ),
    )
