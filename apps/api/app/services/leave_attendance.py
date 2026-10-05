"""請假與出勤的連動（domain_spec M4 / M5：請假生效同時把期間內的出勤改 leave）。

- BACKEND-342：``apply_attendance_for_leave``。移植 ivy
  ``services/student_leave_service.py::apply_attendance_for_leave``：保留「一次範圍處理、不逐日
  N+1」；ivy 以 remark 前綴標記來源改為 ``leave_id`` 欄位；不覆蓋已到班 / 已離班。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from uuid import UUID

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.clock import Clock
from app.models.attendance import StudentAttendance
from app.models.leaves import StudentLeave
from app.realtime.publish import broadcast_after_commit
from app.services.service_calendar import list_service_days


@dataclass(frozen=True)
class LeaveApplyResult:
    applied: list[date]  # 改為 / 建立為 leave 的日期
    skipped_checked_in: list[date]  # 已到班或已離班而保留的日期


def apply_attendance_for_leave(
    session: Session, leave: StudentLeave, *, actor_staff_id: UUID | None, clock: Clock
) -> LeaveApplyResult:
    """過去與今天的營業日：無列建立 leave、expected / absent 改 leave；present / left 保留。

    未來日期由每日初始化（BACKEND-303）依請假直接建立 leave 列。單一 upsert：與每日初始化同時寫
    同一天時，會等待對方的列 commit 後再依 WHERE 條件更新。不 commit。
    """
    end = min(leave.end_date, clock.today())
    days = list_service_days(session, leave.start_date, end) if leave.start_date <= end else []
    if not days:
        return LeaveApplyResult(applied=[], skipped_checked_in=[])

    insert = pg_insert(StudentAttendance).values(
        [
            {
                "student_id": leave.student_id,
                "service_date": d,
                "status": "leave",
                "leave_id": leave.id,
                "updated_by": actor_staff_id,
            }
            for d in days
        ]
    )
    upsert = insert.on_conflict_do_update(
        index_elements=["student_id", "service_date"],
        set_={
            "status": "leave",
            "leave_id": insert.excluded.leave_id,
            "updated_by": insert.excluded.updated_by,
            "check_in_at": None,
            "check_in_source": None,
            "check_out_at": None,
            "check_out_source": None,
        },
        where=StudentAttendance.status.in_(("expected", "absent")),
    ).returning(StudentAttendance.service_date)
    applied_set = set(session.execute(upsert).scalars())

    applied = [d for d in days if d in applied_set]
    if applied:
        broadcast_after_commit(
            session,
            topic="attendance",
            type="attendance.bulk_updated",
            data={"student_id": leave.student_id, "dates": applied},
            clock=clock,
        )
    return LeaveApplyResult(
        applied=applied, skipped_checked_in=[d for d in days if d not in applied_set]
    )
