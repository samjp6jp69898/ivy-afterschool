"""出勤 service（domain_spec M4）。

- BACKEND-302：``ensure_attendance_row``（取得或建立單一學生某日出勤列，check_in / mark_absent /
  batch_check_in 共用）。
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.attendance import StudentAttendance
from app.models.leaves import StudentLeave


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
