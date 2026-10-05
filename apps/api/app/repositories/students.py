"""BACKEND-133：學生 / 班級共用查詢（多個 service 與營運模組共用）。

``Student`` 的 ``school`` / ``class_`` 為 ``lazy="joined"``（outer join），``for_update`` 時
只鎖 students（``FOR UPDATE OF students``）：Postgres 不允許鎖 outer join 的可空側。
``for_update`` 另帶 ``populate_existing``，已載入的物件會被刷新成上鎖後的 DB 值。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.models.classes import SchoolClass
from app.models.students import Student


def get_student_or_404(
    session: Session,
    student_id: UUID,
    *,
    include_archived: bool = False,
    for_update: bool = False,
) -> Student:
    stmt = select(Student).where(Student.id == student_id)
    if not include_archived:
        stmt = stmt.where(Student.archived_at.is_(None))
    if for_update:
        # populate_existing：同 session 已載入過該學生時，以上鎖後的 DB 現值覆蓋舊屬性
        stmt = stmt.with_for_update(of=Student).execution_options(populate_existing=True)
    student = session.execute(stmt).unique().scalar_one_or_none()
    if student is None:
        raise NotFoundError("student_not_found", "找不到學生")
    return student


def get_class_or_404(
    session: Session,
    class_id: UUID,
    *,
    include_archived: bool = False,
    for_update: bool = False,
) -> SchoolClass:
    """``for_update``：寫入前鎖班級列（FOR UPDATE OF classes + populate_existing），同一班的
    改名 / 封存 / 指派員工與學生 INSERT 的 FK KEY SHARE 序列化。"""
    stmt = select(SchoolClass).where(SchoolClass.id == class_id)
    if not include_archived:
        stmt = stmt.where(SchoolClass.archived_at.is_(None))
    if for_update:
        stmt = stmt.with_for_update(of=SchoolClass).execution_options(populate_existing=True)
    school_class = session.execute(stmt).scalar_one_or_none()
    if school_class is None:
        raise NotFoundError("class_not_found", "找不到班級")
    return school_class


def list_active_student_ids(
    session: Session,
    *,
    class_id: UUID | None = None,
    grade_levels: Sequence[int] | None = None,
) -> list[UUID]:
    """status='active' 且未封存的學生 id；可依班級與年級篩選（空的 grade_levels 回空清單）。"""
    stmt = select(Student.id).where(Student.status == "active", Student.archived_at.is_(None))
    if class_id is not None:
        stmt = stmt.where(Student.class_id == class_id)
    if grade_levels is not None:
        stmt = stmt.where(Student.grade_level.in_(list(grade_levels)))
    return list(session.execute(stmt.order_by(Student.student_no, Student.id)).scalars())


@dataclass(frozen=True)
class StudentBrief:
    id: UUID
    student_no: str
    name: str
    grade_level: int
    class_id: UUID | None
    class_name: str | None
    status: str


def student_brief_map(session: Session, student_ids: Iterable[UUID]) -> dict[UUID, StudentBrief]:
    """一次查詢（outer join classes）；不存在的 id 不在結果內，封存學生仍回傳。"""
    ids = set(student_ids)
    if not ids:
        return {}
    rows = session.execute(
        select(
            Student.id,
            Student.student_no,
            Student.name,
            Student.grade_level,
            Student.class_id,
            SchoolClass.name,
            Student.status,
        )
        .outerjoin(SchoolClass, SchoolClass.id == Student.class_id)
        .where(Student.id.in_(ids))
    )
    return {
        row[0]: StudentBrief(
            id=row[0],
            student_no=row[1],
            name=row[2],
            grade_level=row[3],
            class_id=row[4],
            class_name=row[5],
            status=row[6],
        )
        for row in rows
    }
