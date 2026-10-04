"""BACKEND-135：班級服務（清單含人數、負責員工、「我的班」篩選）。"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import CurrentStaff
from app.models.account import StaffUser
from app.models.classes import ClassStaff, SchoolClass
from app.models.students import Student
from app.schemas.classes import ClassListQuery, ClassOut, ClassStaffOut


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
