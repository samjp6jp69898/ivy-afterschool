"""BACKEND-135：班級服務（清單含人數、負責員工、「我的班」篩選）。"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import CurrentStaff
from app.core.errors import ConflictError
from app.models.account import StaffUser
from app.models.classes import ClassStaff, SchoolClass
from app.models.students import Student
from app.repositories.students import get_class_or_404
from app.schemas.classes import ClassCreateIn, ClassListQuery, ClassOut, ClassStaffOut


def list_classes(session: Session, query: ClassListQuery, *, actor: CurrentStaff) -> list[ClassOut]:
    """預設不含封存；排序 academic_year desc、sort_order、name。

    student_count 只計 status active 且未封存的學生（單一 group by 子查詢）；staff 只列啟用中員工
    （lead 在前，再依姓名）。
    """
    counts = (
        select(Student.class_id.label("class_id"), func.count().label("n"))
        .where(Student.status == "active", Student.archived_at.is_(None))
        .group_by(Student.class_id)
        .subquery()
    )
    stmt = (
        select(SchoolClass, func.coalesce(counts.c.n, 0))
        .outerjoin(counts, counts.c.class_id == SchoolClass.id)
        .order_by(SchoolClass.academic_year.desc(), SchoolClass.sort_order, SchoolClass.name)
    )
    if not query.include_archived:
        stmt = stmt.where(SchoolClass.archived_at.is_(None))
    if query.academic_year is not None:
        stmt = stmt.where(SchoolClass.academic_year == query.academic_year)
    if query.mine:
        stmt = stmt.where(
            SchoolClass.id.in_(
                select(ClassStaff.class_id).where(ClassStaff.staff_user_id == actor.id)
            )
        )
    rows = session.execute(stmt).all()
    staff_by_class = _active_staff(session, [klass.id for klass, _ in rows])
    return [
        ClassOut(
            id=klass.id,
            name=klass.name,
            grade_levels=list(klass.grade_levels),
            academic_year=klass.academic_year,
            sort_order=klass.sort_order,
            archived_at=klass.archived_at,
            student_count=int(count),
            staff=staff_by_class.get(klass.id, []),
        )
        for klass, count in rows
    ]


def _active_staff(session: Session, class_ids: list[UUID]) -> dict[UUID, list[ClassStaffOut]]:
    result: dict[UUID, list[ClassStaffOut]] = {}
    if not class_ids:
        return result
    rows = session.execute(
        select(ClassStaff.class_id, StaffUser.id, StaffUser.display_name, ClassStaff.role)
        .join(StaffUser, StaffUser.id == ClassStaff.staff_user_id)
        .where(ClassStaff.class_id.in_(class_ids), StaffUser.is_active.is_(True))
        # role：'assistant' < 'lead'，desc 讓 lead 在前
        .order_by(ClassStaff.role.desc(), StaffUser.display_name, StaffUser.id)
    )
    for class_id, staff_id, display_name, role in rows:
        result.setdefault(class_id, []).append(
            ClassStaffOut(staff_user_id=staff_id, display_name=display_name, role=role)
        )
    return result


_UNIQUE_VIOLATION = "23505"


def get_class(session: Session, class_id: UUID) -> ClassOut:
    """封存班也可查；不存在 → 404 ``class_not_found``。"""
    klass = get_class_or_404(session, class_id, include_archived=True)
    count = session.execute(
        select(func.count())
        .select_from(Student)
        .where(
            Student.class_id == klass.id, Student.status == "active", Student.archived_at.is_(None)
        )
    ).scalar_one()
    return _class_out(klass, int(count), _active_staff(session, [klass.id]).get(klass.id, []))


def create_class(session: Session, data: ClassCreateIn) -> ClassOut:
    """同學年度未封存班名（不分大小寫、去頭尾空白）重複 → 409 ``class_name_taken``。"""
    klass = SchoolClass(
        name=data.name,
        grade_levels=list(data.grade_levels),
        academic_year=data.academic_year,
        sort_order=data.sort_order,
    )
    try:
        with session.begin_nested():
            session.add(klass)
            session.flush()
    except IntegrityError as exc:
        if getattr(exc.orig, "sqlstate", None) != _UNIQUE_VIOLATION:
            raise
        raise ConflictError("class_name_taken", "同學年度已有相同名稱的班級") from None
    return _class_out(klass, 0, [])


def _class_out(klass: SchoolClass, student_count: int, staff: list[ClassStaffOut]) -> ClassOut:
    return ClassOut(
        id=klass.id,
        name=klass.name,
        grade_levels=list(klass.grade_levels),
        academic_year=klass.academic_year,
        sort_order=klass.sort_order,
        archived_at=klass.archived_at,
        student_count=student_count,
        staff=staff,
    )
