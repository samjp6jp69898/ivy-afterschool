"""請假 service（domain_spec M5）。

- BACKEND-347：``list_leaves``（後台列表，篩選 + 分頁）。
- BACKEND-350：``get_attachment_url``（後台簽發附件短效 URL）。
- BACKEND-348：``list_child_leaves``（家長端小孩請假列表，附件短效 URL；呼叫端已驗證所有權）。
- BACKEND-344：``notify_leave_event``（請假建立 / 取消通知班級負責員工與 leaves:read 員工）。
- BACKEND-345：``create_leave``（家長 / 員工建立請假；重疊 409、套用出勤、通知）。
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any, Final, Literal
from uuid import UUID

from sqlalchemy import Select, literal, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.clock import Clock
from app.core.errors import AppError, ConflictError, NotFoundError
from app.core.pagination import Page, PageParams, paginate
from app.core.permissions import Permission
from app.core.settings_registry import LEAVE_WINDOW
from app.core.storage import Storage, StorageError
from app.models.account import StaffUser
from app.models.leaves import (
    EXCLUSION_LEAVE_OVERLAP,
    LEAVE_TYPE_LABELS,
    StudentLeave,
    StudentLeaveAttachment,
)
from app.models.parents import ParentAccount
from app.models.students import Student
from app.notifications.events import Event
from app.notifications.recipients import staff_recipients
from app.notifications.service import enqueue
from app.repositories.students import get_student_or_404, student_brief_map
from app.schemas.leaves import (
    AttachmentUrlOut,
    LeaveAttachmentOut,
    LeaveCreateIn,
    LeaveListQuery,
    LeaveOut,
    LeaveStudentOut,
    ParentLeaveAttachmentOut,
    ParentLeaveCreateIn,
    ParentLeaveOut,
)
from app.services.audit_service import Actor
from app.services.leave_attendance import apply_attendance_for_leave
from app.services.parent_scope import assert_parent_owns_student
from app.services.service_calendar import list_service_days
from app.services.settings_service import get_setting

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


def notify_leave_event(
    session: Session,
    leave: StudentLeave,
    event: Literal[Event.LEAVE_CREATED, Event.LEAVE_CANCELLED],
    *,
    date_range: tuple[date, date] | None = None,
    clock: Clock,
) -> None:
    """create / cancel 共用：通知班級負責員工與有 leaves:read 的員工（in_app + ws）。

    部分取消時呼叫端以 date_range 傳入被取消的區間；收件人為空不呼叫 enqueue。移植 ivy
    ``services/student_leave_notify.py::notify_student_leave`` / ``resolve_recipients``；ivy commit
    後另開 session 解析收件人改為同交易 enqueue（BACKEND-206 本身就在 commit 後才派送）。
    """
    student_name, class_id = session.execute(
        select(Student.name, Student.class_id).where(Student.id == leave.student_id)
    ).one()
    recipients = staff_recipients(
        session,
        permission=Permission.LEAVES_READ,
        class_ids=[class_id] if class_id is not None else [],
    )
    if not recipients:
        return
    start, end = date_range or (leave.start_date, leave.end_date)
    enqueue(
        session,
        event,
        recipients=recipients,
        payload={
            "student_id": leave.student_id,
            "student_name": student_name,
            "leave_id": leave.id,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "leave_type_label": LEAVE_TYPE_LABELS[leave.leave_type],
        },
        clock=clock,
    )


def _actor_id(actor: Actor) -> UUID:
    if actor.id is None:
        raise ValueError(f"actor.type={actor.type} 必須帶 actor.id")
    return actor.id


def create_leave(
    session: Session,
    data: LeaveCreateIn | ParentLeaveCreateIn,
    *,
    actor: Actor,
    clock: Clock,
) -> StudentLeave:
    """家長送出即生效、員工可代登記；套用期間內出勤並通知員工。

    同一學生 active 請假不可重疊由 DB exclusion constraint 把關：連點或兩位家長同時送出時，後到的
    交易在前者 commit 後拋 23P01 → 409 leave_overlap（以 savepoint 包住，不污染外層交易）。去掉 ivy
    的審核流程、娃娃車同步、報表快取失效與 client_request_id。
    """
    actor_id = _actor_id(actor)
    if actor.type == "parent":
        assert_parent_owns_student(session, actor_id, data.student_id, for_write=True)
        window = get_setting(session, LEAVE_WINDOW)
        today = clock.today()
        earliest = today - timedelta(days=window.past_days)
        latest = today + timedelta(days=window.future_days)
        if not earliest <= data.start_date <= latest:
            raise AppError(
                "leave_date_out_of_window",
                f"請假開始日須在今天前 {window.past_days} 天到後 {window.future_days} 天之間",
                status=422,
                details={"past_days": window.past_days, "future_days": window.future_days},
            )
    else:
        student = get_student_or_404(session, data.student_id)
        if student.status != "active":
            raise ConflictError("student_not_active", "學生目前不在學，無法登記請假")
    if not list_service_days(session, data.start_date, data.end_date):
        raise AppError("no_service_days_in_range", "請假期間沒有營業日", status=422)

    leave = StudentLeave(
        student_id=data.student_id,
        leave_type=data.leave_type,
        start_date=data.start_date,
        end_date=data.end_date,
        reason=data.reason,
        status="active",
        created_by_type=actor.type,
        created_by_id=actor_id,
    )
    try:
        with session.begin_nested():
            session.add(leave)
            session.flush()
    except IntegrityError as exc:
        orig = exc.orig
        constraint = getattr(getattr(orig, "diag", None), "constraint_name", None)
        if getattr(orig, "sqlstate", None) != "23P01" or constraint != EXCLUSION_LEAVE_OVERLAP:
            raise
        existing = session.execute(
            select(StudentLeave.id, StudentLeave.start_date, StudentLeave.end_date)
            .where(
                StudentLeave.student_id == data.student_id,
                StudentLeave.status == "active",
                StudentLeave.start_date <= data.end_date,
                StudentLeave.end_date >= data.start_date,
            )
            .order_by(StudentLeave.start_date)
            .limit(1)
        ).one()
        raise ConflictError(
            "leave_overlap",
            "這段期間已經有請假",
            details={
                "leave_id": existing.id,
                "start_date": existing.start_date,
                "end_date": existing.end_date,
            },
        ) from exc

    apply_attendance_for_leave(
        session, leave, actor_staff_id=actor_id if actor.type == "staff" else None, clock=clock
    )
    notify_leave_event(session, leave, Event.LEAVE_CREATED, clock=clock)
    return leave
