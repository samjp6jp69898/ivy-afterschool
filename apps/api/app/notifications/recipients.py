"""BACKEND-204 / 205：通知收件人解析（domain_spec M9）。

收件人 = 該學生 ``receives_notifications = true`` 且已綁定的 guardian 帳號；guardian 與學生
皆未封存、家長帳號 ``status = 'active'``。同一家長去重，依 parent id 排序。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.permissions import Permission, resolve_effective_permissions
from app.models.account import StaffUser
from app.models.classes import ClassStaff
from app.models.parents import Guardian, ParentAccount
from app.models.students import Student


@dataclass(frozen=True)
class Recipient:
    type: Literal["staff", "parent"]
    id: UUID


def parent_recipients(session: Session, student_id: UUID) -> list[Recipient]:
    return parent_recipients_bulk(session, [student_id])[student_id]


def parent_recipients_bulk(
    session: Session, student_ids: Iterable[UUID]
) -> dict[UUID, list[Recipient]]:
    """一次查詢；沒有收件人的學生回空 list（key 仍存在）。"""
    ids = list(dict.fromkeys(student_ids))
    result: dict[UUID, list[Recipient]] = {sid: [] for sid in ids}
    if not ids:
        return result
    rows = session.execute(
        select(Guardian.student_id, Guardian.parent_account_id)
        .join(Student, Student.id == Guardian.student_id)
        .join(ParentAccount, ParentAccount.id == Guardian.parent_account_id)
        .where(
            Guardian.student_id.in_(ids),
            Guardian.receives_notifications.is_(True),
            Guardian.archived_at.is_(None),
            Student.archived_at.is_(None),
            ParentAccount.status == "active",
        )
        .distinct()
        .order_by(Guardian.student_id, Guardian.parent_account_id)
    )
    for student_id, parent_id in rows:
        if parent_id is not None:  # inner join 已排除 null，供型別收斂
            result[student_id].append(Recipient("parent", parent_id))
    return result


def staff_recipients(
    session: Session,
    *,
    permission: Permission | None = None,
    class_ids: Iterable[UUID] = (),
) -> list[Recipient]:
    """（啟用中且有效權限含 permission 的員工）加上（class_ids 任一班的 class_staff 中啟用的員工）。

    有效權限以 ``resolve_effective_permissions`` 計算（含 admin ``*`` 展開、revoked 排除）；
    StaffUser.role 為 joined 載入，一次查詢取回全部角色，不 N+1。去重、依 id 排序。
    """
    ids: set[UUID] = set()
    if permission is not None:
        for staff in (
            session.execute(select(StaffUser).where(StaffUser.is_active.is_(True)))
            .unique()
            .scalars()
        ):
            effective = resolve_effective_permissions(
                staff.role.permissions, staff.extra_permissions, staff.revoked_permissions
            )
            if str(permission) in effective:
                ids.add(staff.id)
    class_id_list = list(dict.fromkeys(class_ids))
    if class_id_list:
        ids.update(
            session.execute(
                select(ClassStaff.staff_user_id)
                .join(StaffUser, StaffUser.id == ClassStaff.staff_user_id)
                .where(ClassStaff.class_id.in_(class_id_list), StaffUser.is_active.is_(True))
            ).scalars()
        )
    return [Recipient("staff", staff_id) for staff_id in sorted(ids)]
