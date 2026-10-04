"""BACKEND-149：學生服務（清單搜尋、篩選、分頁）。
BACKEND-150：``get_student`` 學生詳情（移植 ivy ``api/students.py::get_student`` /
``get_student_medical`` 的「敏感欄位另行授權」；去掉醫療多欄位、lifecycle）。

- 封存學生也可查（include_archived）；不存在 404 ``student_not_found``。
- ``has_id_number`` / ``has_health_note`` 一律回傳；``sensitive`` 只在 actor 有
  ``students:sensitive`` 時以 BACKEND-009 解密填入，否則 None；解密失敗 → 該欄位 None 並
  logger.error（只記學生 id 與欄位名，不記密文）。
- ``photo_url``：photo_path 有值時簽 300 秒短效 URL；StorageError → None 並記 log，不讓詳情頁失敗。
- ``guardians``：BACKEND-168 ``list_for_student``（未封存監護人、含綁定狀態；需要 clock 判斷綁定碼
  是否過期，故本函式多收 ``clock``）。
"""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.deps import CurrentStaff
from app.core.clock import Clock
from app.core.crypto import DecryptionError, decrypt_bytes
from app.core.errors import AppError
from app.core.pagination import Page, PageParams, paginate
from app.core.permissions import Permission
from app.core.storage import Storage, StorageError
from app.models.classes import SchoolClass
from app.models.students import Student
from app.repositories.students import get_student_or_404
from app.schemas.students import (
    StudentDetailOut,
    StudentListItemOut,
    StudentListQuery,
    StudentSensitiveOut,
)
from app.services.guardian_service import list_for_student
from app.services.students.id_number import (
    id_number_hmac,
    normalize_id_number,
    validate_id_number,
)

logger = logging.getLogger(__name__)

PHOTO_URL_TTL_SECONDS = 300


def list_students(
    session: Session, query: StudentListQuery, page: PageParams, *, actor: CurrentStaff
) -> Page[StudentListItemOut]:
    """q：學號前綴（不分大小寫）或姓名模糊；有 students:sensitive 且 q 是合法身分證時另比對 HMAC。

    沒有 sensitive 權限時不做身分證比對，避免以搜尋結果推測身分證。排序 grade_level、班級名稱
    （無班級在後）、student_no。預設排除封存。
    """
    stmt = select(Student).outerjoin(SchoolClass, SchoolClass.id == Student.class_id)
    if not query.include_archived:
        stmt = stmt.where(Student.archived_at.is_(None))
    if query.q:
        conditions = [
            Student.student_no.istartswith(query.q, autoescape=True),
            Student.name.icontains(query.q, autoescape=True),
        ]
        if actor.has(Permission.STUDENTS_SENSITIVE) and _is_id_number(query.q):
            conditions.append(
                Student.id_number_hmac == id_number_hmac(normalize_id_number(query.q))
            )
        stmt = stmt.where(or_(*conditions))
    if query.class_id is not None:
        stmt = stmt.where(Student.class_id == query.class_id)
    if query.grade_level is not None:
        stmt = stmt.where(Student.grade_level == query.grade_level)
    if query.status is not None:
        stmt = stmt.where(Student.status == query.status)
    if query.school_id is not None:
        stmt = stmt.where(Student.school_id == query.school_id)

    rows, total = paginate(
        session,
        stmt.order_by(
            Student.grade_level, SchoolClass.name.asc().nulls_last(), Student.student_no, Student.id
        ),
        page,
    )
    return Page(items=[StudentListItemOut.model_validate(row) for row in rows], total=total)


def _is_id_number(q: str) -> bool:
    try:
        validate_id_number(normalize_id_number(q))
    except AppError:
        return False
    return True


def _decrypt_field(student_id: UUID, field: str, blob: bytes | None) -> str | None:
    if blob is None:
        return None
    try:
        return decrypt_bytes(blob)
    except DecryptionError:
        logger.error("學生 %s 的 %s 解密失敗，視為未設定", student_id, field)
        return None


def _sensitive(student: Student, actor: CurrentStaff) -> StudentSensitiveOut | None:
    if not actor.has(Permission.STUDENTS_SENSITIVE):
        return None
    return StudentSensitiveOut(
        id_number=_decrypt_field(student.id, "id_number", student.id_number_enc),
        health_note=_decrypt_field(student.id, "health_note", student.health_note_enc),
    )


def _photo_url(student: Student, storage: Storage) -> str | None:
    if not student.photo_path:
        return None
    try:
        return storage.create_signed_url(
            "student-photos", student.photo_path, PHOTO_URL_TTL_SECONDS
        )
    except StorageError:
        logger.warning("學生 %s 的照片簽名 URL 失敗，詳情頁不帶照片", student.id, exc_info=True)
        return None


def get_student(
    session: Session,
    student_id: UUID,
    *,
    actor: CurrentStaff,
    storage: Storage,
    clock: Clock,
) -> StudentDetailOut:
    student = get_student_or_404(session, student_id, include_archived=True)
    base = StudentListItemOut.model_validate(student).model_dump(by_alias=True)
    return StudentDetailOut(
        **base,
        birthday=student.birthday,
        enrolled_on=student.enrolled_on,
        withdrawn_on=student.withdrawn_on,
        note=student.note,
        photo_url=_photo_url(student, storage),
        has_id_number=student.id_number_enc is not None,
        has_health_note=student.health_note_enc is not None,
        sensitive=_sensitive(student, actor),
        guardians=list_for_student(session, student.id, clock=clock),
    )
