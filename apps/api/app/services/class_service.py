"""BACKEND-135：班級服務（清單含人數、負責員工、「我的班」篩選）。
BACKEND-136 ``get_class``、BACKEND-137 ``create_class``。
BACKEND-138 ``update_class``：只更新有給的欄位；封存班 409 ``class_archived``、同學年同名 409
``class_name_taken``（移植 ivy ``api/classrooms.py::update_classroom``，去掉教師固定欄位與學期）。
BACKEND-139 ``archive_class``：仍有在學（active / suspended、未封存）學生 → 409
``class_has_students``；已封存冪等；class_staff 保留（移植 ivy ``delete_classroom`` 的在學學生
守衛）。
BACKEND-140 ``set_class_staff``：整批取代負責員工，以差異更新（保留既有列 id）；員工必須存在且啟用。
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import CurrentStaff
from app.core.clock import Clock
from app.core.errors import AppError, ConflictError
from app.models.account import StaffUser
from app.models.classes import ClassStaff, SchoolClass
from app.models.students import Student
from app.repositories.students import get_class_or_404
from app.schemas.classes import (
    ClassCreateIn,
    ClassListQuery,
    ClassOut,
    ClassStaffOut,
    ClassStaffPutIn,
    ClassUpdateIn,
)

_UNIQUE_VIOLATION = "23505"
# 封存時仍算「在學」的狀態（withdrawn 不阻擋封存）
_ENROLLED_STATUSES = ("active", "suspended")


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
        _class_out(klass, int(count), staff_by_class.get(klass.id, [])) for klass, count in rows
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


def _student_count(session: Session, class_id: UUID, statuses: tuple[str, ...]) -> int:
    return int(
        session.execute(
            select(func.count())
            .select_from(Student)
            .where(
                Student.class_id == class_id,
                Student.status.in_(statuses),
                Student.archived_at.is_(None),
            )
        ).scalar_one()
    )


def _single_class_out(session: Session, klass: SchoolClass) -> ClassOut:
    return _class_out(
        klass,
        _student_count(session, klass.id, ("active",)),
        _active_staff(session, [klass.id]).get(klass.id, []),
    )


def get_class(session: Session, class_id: UUID) -> ClassOut:
    """封存班也可查；不存在 → 404 ``class_not_found``。"""
    klass = get_class_or_404(session, class_id, include_archived=True)
    return _single_class_out(session, klass)


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


def _get_writable_class(session: Session, class_id: UUID) -> SchoolClass:
    """寫入用：先 FOR UPDATE 鎖班級列（與並發封存序列化、重讀上鎖後的 archived_at），
    不存在 404、已封存 409 ``class_archived``。"""
    klass = get_class_or_404(session, class_id, include_archived=True, for_update=True)
    if klass.archived_at is not None:
        raise ConflictError("class_archived", "班級已封存，無法修改")
    return klass


def update_class(session: Session, class_id: UUID, data: ClassUpdateIn) -> ClassOut:
    """只更新有給的欄位（exclude_unset）；改到同學年既有班名 → 409 ``class_name_taken``。"""
    klass = _get_writable_class(session, class_id)
    try:
        with session.begin_nested():
            for field, value in data.model_dump(exclude_unset=True).items():
                setattr(klass, field, value)
            session.flush()
    except IntegrityError as exc:
        if getattr(exc.orig, "sqlstate", None) != _UNIQUE_VIOLATION:
            raise
        raise ConflictError("class_name_taken", "同學年度已有相同名稱的班級") from None
    return _single_class_out(session, klass)


def archive_class(session: Session, class_id: UUID, *, clock: Clock) -> ClassOut:
    """封存班級：已封存直接回傳（冪等）；仍有在學學生 → 409 ``class_has_students``。

    先 FOR UPDATE 鎖班級列再計數：學生 INSERT / 改 class_id 的 FK 檢查對班級列持 KEY SHARE，
    未 commit 的新學生會讓這裡等到它 commit 後才計數（READ COMMITTED 下看得到）。
    """
    klass = get_class_or_404(session, class_id, include_archived=True, for_update=True)
    if klass.archived_at is not None:
        return _single_class_out(session, klass)
    enrolled = _student_count(session, klass.id, _ENROLLED_STATUSES)
    if enrolled > 0:
        raise ConflictError(
            "class_has_students",
            "班級仍有在學學生，請先改班或退班後再封存",
            details={"student_count": enrolled},
        )
    klass.archived_at = clock.now()
    session.flush()
    return _single_class_out(session, klass)


def set_class_staff(session: Session, class_id: UUID, data: ClassStaffPutIn) -> ClassOut:
    """整批取代負責員工（domain_spec §2 ``PUT /classes/{id}/staff``）。

    差異更新：刪除不在新清單的列、更新 role、新增新列（保留既有列 id）。任一 staff_user_id
    不存在或停用 → 422 ``invalid_staff``，details.invalid 列出無效 id。
    """
    klass = _get_writable_class(session, class_id)
    wanted = {item.staff_user_id: item.role for item in data.items}
    if wanted:
        active_ids = set(
            session.execute(
                select(StaffUser.id).where(StaffUser.id.in_(wanted), StaffUser.is_active.is_(True))
            ).scalars()
        )
        invalid = [staff_id for staff_id in wanted if staff_id not in active_ids]
        if invalid:
            raise AppError(
                "invalid_staff",
                "部分員工不存在或已停用",
                status=422,
                details={"invalid": invalid},
            )

    # staff_links 為 delete-orphan：從集合移除即刪列
    for link in list(klass.staff_links):
        role = wanted.get(link.staff_user_id)
        if role is None:
            klass.staff_links.remove(link)
        elif link.role != role:
            link.role = role
    existing = {link.staff_user_id for link in klass.staff_links}
    for staff_id, role in wanted.items():
        if staff_id not in existing:
            klass.staff_links.append(ClassStaff(staff_user_id=staff_id, role=role))
    session.flush()
    return _single_class_out(session, klass)


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
