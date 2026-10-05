"""出勤 service（domain_spec M4）。

- BACKEND-302：``ensure_attendance_row``（取得或建立單一學生某日出勤列，check_in / mark_absent /
  batch_check_in 共用）。
- BACKEND-310：``amend_attendance``（改判已登記出勤，寫 audit，commit 後推播 admin / 家長）。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.clock import Clock, to_taipei
from app.core.errors import AppError, ConflictError, NotFoundError
from app.models.attendance import StudentAttendance
from app.models.leaves import LEAVE_TYPE_LABELS, StudentLeave
from app.realtime.publish import broadcast_after_commit
from app.repositories.students import StudentBrief, student_brief_map
from app.schemas.attendance import (
    AttendanceAmendIn,
    AttendanceRowOut,
    LeaveBriefOut,
    to_parent_attendance_event,
)
from app.services.audit_service import Actor, record

if TYPE_CHECKING:
    from app.api.deps import CurrentStaff
    from app.core.request_meta import RequestMeta


def _active_leave_id(session: Session, student_id: UUID, service_date: date) -> UUID | None:
    """涵蓋 service_date 的 active 請假（DB exclusion constraint 保證至多一筆）。"""
    return session.execute(
        select(StudentLeave.id).where(
            StudentLeave.student_id == student_id,
            StudentLeave.status == "active",
            StudentLeave.start_date <= service_date,
            StudentLeave.end_date >= service_date,
        )
    ).scalar_one_or_none()


def ensure_attendance_row(
    session: Session, student_id: UUID, service_date: date, *, for_update: bool = True
) -> StudentAttendance:
    """取得或建立出勤列（冪等）；無列時依請假建立 leave 或 expected，已有列原樣回傳。

    不檢查營業日與學生狀態（由呼叫端負責）；不 commit。兩個交易同時建立時，第二個 INSERT 等第一個
    交易結束後 do nothing，不會拋 IntegrityError；for_update 讓後續的狀態更新在此列上序列化。
    """
    leave_id = _active_leave_id(session, student_id, service_date)
    session.execute(
        pg_insert(StudentAttendance)
        .values(
            student_id=student_id,
            service_date=service_date,
            status="leave" if leave_id is not None else "expected",
            leave_id=leave_id,
        )
        .on_conflict_do_nothing(index_elements=["student_id", "service_date"])
    )
    stmt = select(StudentAttendance).where(
        StudentAttendance.student_id == student_id,
        StudentAttendance.service_date == service_date,
    )
    if for_update:
        stmt = stmt.with_for_update()
    # 已載入的物件以 DB 現值刷新（別的交易可能剛改過）
    return session.execute(stmt.execution_options(populate_existing=True)).scalar_one()


def _leave_brief(leave: StudentLeave) -> LeaveBriefOut:
    return LeaveBriefOut(
        id=leave.id,
        leave_type=leave.leave_type,
        leave_type_label=LEAVE_TYPE_LABELS[leave.leave_type],
        start_date=leave.start_date,
        end_date=leave.end_date,
    )


def _row_out(
    row: StudentAttendance, brief: StudentBrief, leave: StudentLeave | None
) -> AttendanceRowOut:
    return AttendanceRowOut(
        id=row.id,
        student_id=row.student_id,
        student_no=brief.student_no,
        student_name=brief.name,
        grade_level=brief.grade_level,
        class_id=brief.class_id,
        class_name=brief.class_name,
        service_date=row.service_date,
        status=row.status,
        check_in_at=row.check_in_at,
        check_in_source=row.check_in_source,
        check_out_at=row.check_out_at,
        check_out_source=row.check_out_source,
        leave=_leave_brief(leave) if leave is not None else None,
        note=row.note,
        updated_at=row.updated_at,
    )


def _single_row_out(session: Session, row: StudentAttendance) -> AttendanceRowOut:
    brief = student_brief_map(session, [row.student_id])[row.student_id]
    leave = session.get(StudentLeave, row.leave_id) if row.leave_id is not None else None
    return _row_out(row, brief, leave)


def _on_service_date(value: datetime, service_date: date) -> bool:
    try:
        return to_taipei(value).date() == service_date
    except OverflowError:
        # 9999-12-31T23:00-08:00 這類極端值轉台北時間會溢位，必然不在 service_date
        return False


def _snapshot(row: StudentAttendance) -> dict[str, Any]:
    return {
        "status": row.status,
        "check_in_at": row.check_in_at,
        "check_out_at": row.check_out_at,
        "note": row.note,
    }


def amend_attendance(
    session: Session,
    attendance_id: UUID,
    data: AttendanceAmendIn,
    *,
    actor: CurrentStaff,
    meta: RequestMeta,
    clock: Clock,
) -> AttendanceRowOut:
    """改判已登記出勤（未給的欄位沿用原值）；請假列只能由請假的建立 / 取消改變。"""
    row = session.execute(
        select(StudentAttendance)
        .where(StudentAttendance.id == attendance_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError("attendance_not_found", "找不到出勤紀錄")
    if row.status == "leave":
        raise ConflictError("attendance_managed_by_leave", "請假中的出勤只能由請假的建立或取消改變")

    given = data.model_fields_set
    # status=null 與其他欄位一起送時仍在 fields_set 內：視為未提供
    status = data.status if data.status is not None else row.status
    check_in_at = data.check_in_at if "check_in_at" in given else row.check_in_at
    check_out_at = data.check_out_at if "check_out_at" in given else row.check_out_at
    note = data.note if "note" in given else row.note

    if status in ("expected", "absent"):
        check_in_at = check_out_at = None
    elif status == "present":
        if check_in_at is None:
            raise AppError("check_in_required", "已到班需要到班時間", status=422)
        check_out_at = None
    elif check_in_at is None or check_out_at is None:
        raise AppError("check_out_required", "已離班需要到班與離班時間", status=422)

    if check_in_at is not None and check_out_at is not None and check_out_at < check_in_at:
        raise AppError("invalid_times", "離班時間不可早於到班時間", status=422)
    for value in (check_in_at, check_out_at):
        if value is not None and not _on_service_date(value, row.service_date):
            raise AppError("time_not_on_service_date", "時間必須在該出勤日當天", status=422)

    before = _snapshot(row)
    after = {
        "status": status,
        "check_in_at": check_in_at,
        "check_out_at": check_out_at,
        "note": note,
    }
    if after == before:
        raise AppError("no_changes", "內容與原紀錄相同", status=422)

    # 時間有變動的那一側 source 設為 manual；未變動者保留原 source，清空時一併清空
    if check_in_at != row.check_in_at:
        row.check_in_source = "manual" if check_in_at is not None else None
    if check_out_at != row.check_out_at:
        row.check_out_source = "manual" if check_out_at is not None else None
    row.status = status
    row.check_in_at = check_in_at
    row.check_out_at = check_out_at
    row.note = note
    row.updated_by = actor.id
    session.flush()

    record(
        session,
        actor=Actor.staff(actor),
        action="attendance.amend",
        entity_type="student_attendance",
        entity_id=row.id,
        before=before,
        after={**after, "reason": data.reason},
        meta=meta,
    )
    out = _single_row_out(session, row)
    broadcast_after_commit(
        session,
        topic="attendance",
        type="attendance.updated",
        data=out.model_dump(),
        student_id=row.student_id,
        parent_data=to_parent_attendance_event(row),
        clock=clock,
    )
    return out
