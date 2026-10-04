"""BACKEND-204：家長通知收件人解析（domain_spec M9）。

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
