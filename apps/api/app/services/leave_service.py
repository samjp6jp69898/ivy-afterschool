"""請假 service（domain_spec M5）。

- BACKEND-347：``list_leaves``（後台列表，篩選 + 分頁）。
- BACKEND-350：``get_attachment_url``（後台簽發附件短效 URL）。
- BACKEND-348：``list_child_leaves``（家長端小孩請假列表，附件短效 URL；呼叫端已驗證所有權）。
"""

from __future__ import annotations

import logging
from typing import Any, Final
from uuid import UUID

from sqlalchemy import Select, literal, select
from sqlalchemy.orm import Session

from app.core.clock import Clock
from app.core.errors import AppError, NotFoundError
from app.core.pagination import Page, PageParams, paginate
from app.core.storage import Storage, StorageError
from app.models.account import StaffUser
from app.models.leaves import LEAVE_TYPE_LABELS, StudentLeave, StudentLeaveAttachment
from app.models.parents import ParentAccount
from app.models.students import Student
from app.repositories.students import student_brief_map
from app.schemas.leaves import (
    AttachmentUrlOut,
    LeaveAttachmentOut,
    LeaveListQuery,
    LeaveOut,
    LeaveStudentOut,
    ParentLeaveAttachmentOut,
    ParentLeaveOut,
)

logger = logging.getLogger(__name__)

ATTACHMENT_URL_SECONDS: Final = 300


def _actor_names(
    session: Session, leaves: list[StudentLeave]
) -> dict[tuple[str, UUID], str | None]:
    """建立 / 取消者名稱（員工與家長合併成一次 UNION 查詢）。"""
    wanted: set[tuple[str, UUID]] = set()
    for leave in leaves:
        wanted.add((leave.created_by_type, leave.created_by_id))
        if leave.cancelled_by_type is not None and leave.cancelled_by_id is not None:
            wanted.add((leave.cancelled_by_type, leave.cancelled_by_id))
    staff_ids = {i for t, i in wanted if t == "staff"}
    parent_ids = {i for t, i in wanted if t == "parent"}
    parts: list[Select[Any, Any, Any]] = []
    if staff_ids:
        parts.append(
            select(literal("staff"), StaffUser.id, StaffUser.display_name).where(
                StaffUser.id.in_(staff_ids)
            )
        )
    if parent_ids:
        parts.append(
            select(literal("parent"), ParentAccount.id, ParentAccount.display_name).where(
                ParentAccount.id.in_(parent_ids)
            )
        )
    if not parts:
        return {}
    stmt = parts[0] if len(parts) == 1 else parts[0].union_all(parts[1])
    return {(row[0], row[1]): row[2] for row in session.execute(stmt)}


def list_leaves(session: Session, query: LeaveListQuery, page: PageParams) -> Page[LeaveOut]:
    """篩選後依 start_date desc、created_at desc 分頁；附件只回 metadata（不簽 URL）。

    SQL 次數固定：count、列表（附件 selectin）、學生、建立 / 取消者名稱。
    """
    stmt = select(StudentLeave)
    if query.class_id is not None:
        stmt = stmt.join(Student, Student.id == StudentLeave.student_id).where(
            Student.class_id == query.class_id
        )
    if query.student_id is not None:
        stmt = stmt.where(StudentLeave.student_id == query.student_id)
    if query.status is not None:
        stmt = stmt.where(StudentLeave.status == query.status)
    if query.leave_type is not None:
        stmt = stmt.where(StudentLeave.leave_type == query.leave_type)
    if query.created_by_type is not None:
        stmt = stmt.where(StudentLeave.created_by_type == query.created_by_type)
    # 區間交集（含頭尾）：start <= date_to 且 end >= date_from
    if query.date_to is not None:
        stmt = stmt.where(StudentLeave.start_date <= query.date_to)
    if query.date_from is not None:
        stmt = stmt.where(StudentLeave.end_date >= query.date_from)
    stmt = stmt.order_by(
        StudentLeave.start_date.desc(), StudentLeave.created_at.desc(), StudentLeave.id
    )

    leaves, total = paginate(session, stmt, page)
    students = student_brief_map(session, {leave.student_id for leave in leaves})
    names = _actor_names(session, leaves)
    items = []
    for leave in leaves:
        brief = students[leave.student_id]
        cancelled_by = (
            names.get((leave.cancelled_by_type, leave.cancelled_by_id))
            if leave.cancelled_by_type is not None and leave.cancelled_by_id is not None
            else None
        )
        items.append(
            LeaveOut(
                id=leave.id,
                student=LeaveStudentOut(
                    id=brief.id,
                    student_no=brief.student_no,
                    name=brief.name,
                    class_name=brief.class_name,
                ),
                leave_type=leave.leave_type,
                leave_type_label=LEAVE_TYPE_LABELS[leave.leave_type],
                start_date=leave.start_date,
                end_date=leave.end_date,
                reason=leave.reason,
                status=leave.status,
                created_by_type=leave.created_by_type,
                created_by_name=names.get((leave.created_by_type, leave.created_by_id)),
                created_at=leave.created_at,
                cancelled_at=leave.cancelled_at,
                cancelled_by_type=leave.cancelled_by_type,
                cancelled_by_name=cancelled_by,
                attachments=[
                    LeaveAttachmentOut(
                        id=a.id,
                        mime_type=a.mime_type,
                        size_bytes=a.size_bytes,
                        created_at=a.created_at,
                    )
                    for a in leave.attachments
                ],
            )
        )
    return Page(items=items, total=total)


def get_attachment_url(
    session: Session, leave_id: UUID, attachment_id: UUID, *, storage: Storage
) -> AttachmentUrlOut:
    attachment = session.execute(
        select(StudentLeaveAttachment).where(
            StudentLeaveAttachment.id == attachment_id,
            StudentLeaveAttachment.leave_id == leave_id,
        )
    ).scalar_one_or_none()
    if attachment is None:
        raise NotFoundError("attachment_not_found", "找不到附件")
    try:
        url = storage.create_signed_url(
            "leave-attachments", attachment.storage_path, ATTACHMENT_URL_SECONDS
        )
    except StorageError as exc:
        logger.warning("請假附件簽名失敗 attachment_id=%s", attachment.id)
        raise AppError(
            "storage_unavailable", "檔案儲存服務暫時無法使用，請稍後再試", status=502
        ) from exc
    return AttachmentUrlOut(url=url, expires_in=ATTACHMENT_URL_SECONDS)


def _signed_url_or_none(storage: Storage, attachment: StudentLeaveAttachment) -> str | None:
    """列表不因單一附件簽名失敗而 500：失敗回 None 並記 warning。"""
    try:
        return storage.create_signed_url(
            "leave-attachments", attachment.storage_path, ATTACHMENT_URL_SECONDS
        )
    except StorageError:
        logger.warning("請假附件簽名失敗 attachment_id=%s", attachment.id)
        return None


def list_child_leaves(
    session: Session, student_id: UUID, page: PageParams, *, storage: Storage, clock: Clock
) -> Page[ParentLeaveOut]:
    """該學生的請假（含已取消），start_date desc、created_at desc；不回傳員工姓名。

    can_cancel = active 且仍有今天或之後的日子（BACKEND-346 取消剩餘日子）。移植 ivy
    ``api/parent_portal/leaves.py::list_leaves``。
    """
    today = clock.today()
    stmt = (
        select(StudentLeave)
        .where(StudentLeave.student_id == student_id)
        .order_by(StudentLeave.start_date.desc(), StudentLeave.created_at.desc(), StudentLeave.id)
    )
    leaves, total = paginate(session, stmt, page)
    items = [
        ParentLeaveOut(
            id=leave.id,
            student_id=leave.student_id,
            leave_type=leave.leave_type,
            leave_type_label=LEAVE_TYPE_LABELS[leave.leave_type],
            start_date=leave.start_date,
            end_date=leave.end_date,
            reason=leave.reason,
            status=leave.status,
            created_by_type=leave.created_by_type,
            created_at=leave.created_at,
            cancelled_at=leave.cancelled_at,
            can_cancel=leave.status == "active" and leave.end_date >= today,
            attachments=[
                ParentLeaveAttachmentOut(
                    id=a.id,
                    mime_type=a.mime_type,
                    size_bytes=a.size_bytes,
                    created_at=a.created_at,
                    url=_signed_url_or_none(storage, a),
                )
                for a in leave.attachments
            ],
        )
        for leave in leaves
    ]
    return Page(items=items, total=total)
