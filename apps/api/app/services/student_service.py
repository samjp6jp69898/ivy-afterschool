"""BACKEND-149：學生服務（清單搜尋、篩選、分頁）。"""

from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.deps import CurrentStaff
from app.core.errors import AppError
from app.core.pagination import Page, PageParams, paginate
from app.core.permissions import Permission
from app.models.classes import SchoolClass
from app.models.students import Student
from app.schemas.students import StudentListItemOut, StudentListQuery
from app.services.students.id_number import (
    id_number_hmac,
    normalize_id_number,
    validate_id_number,
)


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
