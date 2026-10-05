"""後台出勤 endpoint（domain_spec M5）。寫入型 handler 呼叫 service 後自行 ``db.commit()``。

- BACKEND-315 ``GET /attendance/daily``：attendance:read；``DailyAttendanceQuery`` →
  ``DailyAttendanceOut``。
- BACKEND-321 ``GET /attendance/monthly``：attendance:read；``MonthlyAttendanceQuery`` →
  ``MonthlyAttendanceOut``。
- BACKEND-322 ``GET /attendance/monthly/export``：attendance:read；同上查詢 → BACKEND-313 xlsx，
  ``Content-Disposition: attachment; filename*=UTF-8''出勤月報_{班名|全部}_{month}.xlsx``、
  ``Cache-Control: no-store``；class_id 不存在 → 404 ``class_not_found``。
- BACKEND-316 ``POST /attendance/{student_id}/check-in``：attendance:operate；``CheckInIn`` 可省略 →
  BACKEND-305 → ``AttendanceRowOut``。
- BACKEND-317 ``POST /attendance/{student_id}/check-out``：attendance:operate；``CheckOutIn`` 可省略
  → BACKEND-306（present → left；未到班 409 ``not_checked_in``）。
- BACKEND-319 ``POST /attendance/{student_id}/mark-absent``：attendance:operate（一般點名動作）；
  ``MarkAbsentIn`` 可省略 → BACKEND-309。
- BACKEND-320 ``PATCH /attendance/{attendance_id}``：attendance:amend；``AttendanceAmendIn`` +
  request meta → BACKEND-310（寫 audit）。

固定路徑（/daily、/monthly、/monthly/export）宣告在 ``/{student_id}/...`` 與 ``/{attendance_id}``
之前。
"""

from __future__ import annotations

from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.api.admin._query import query_model
from app.api.deps import CurrentStaff, require_permission
from app.core.clock import Clock, get_clock
from app.core.db import get_db
from app.core.permissions import Permission
from app.core.request_meta import RequestMeta, get_request_meta
from app.repositories.students import get_class_or_404
from app.schemas.attendance import (
    AttendanceAmendIn,
    AttendanceRowOut,
    CheckInIn,
    CheckOutIn,
    DailyAttendanceOut,
    DailyAttendanceQuery,
    MarkAbsentIn,
    MonthlyAttendanceOut,
    MonthlyAttendanceQuery,
)
from app.services import attendance_service
from app.services.attendance_export import build_monthly_attendance_xlsx

router = APIRouter(prefix="/attendance", tags=["admin-attendance"])

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_ALL_CLASSES_TITLE = "全部"

AttendanceRead = Annotated[CurrentStaff, Depends(require_permission(Permission.ATTENDANCE_READ))]
AttendanceOperate = Annotated[
    CurrentStaff, Depends(require_permission(Permission.ATTENDANCE_OPERATE))
]
AttendanceAmend = Annotated[CurrentStaff, Depends(require_permission(Permission.ATTENDANCE_AMEND))]
Db = Annotated[Session, Depends(get_db)]
ClockDep = Annotated[Clock, Depends(get_clock)]


@router.get("/daily", response_model=DailyAttendanceOut)
def get_daily(
    _: AttendanceRead,
    query: Annotated[DailyAttendanceQuery, Depends(query_model(DailyAttendanceQuery))],
    db: Db,
    clock: ClockDep,
) -> DailyAttendanceOut:
    return attendance_service.get_daily_attendance(db, query, clock=clock)


@router.get("/monthly", response_model=MonthlyAttendanceOut)
def get_monthly(
    _: AttendanceRead,
    query: Annotated[MonthlyAttendanceQuery, Depends(query_model(MonthlyAttendanceQuery))],
    db: Db,
    clock: ClockDep,
) -> MonthlyAttendanceOut:
    return attendance_service.get_monthly_attendance(db, query, clock=clock)


@router.get("/monthly/export", response_class=Response)
def export_monthly(
    _: AttendanceRead,
    query: Annotated[MonthlyAttendanceQuery, Depends(query_model(MonthlyAttendanceQuery))],
    db: Db,
    clock: ClockDep,
) -> Response:
    title = _ALL_CLASSES_TITLE
    if query.class_id is not None:
        title = get_class_or_404(db, query.class_id, include_archived=True).name
    report = attendance_service.get_monthly_attendance(db, query, clock=clock)
    content = build_monthly_attendance_xlsx(report, title_class_name=title)
    filename = quote(f"出勤月報_{title}_{report.month}.xlsx")
    return Response(
        content=content,
        media_type=XLSX_MEDIA_TYPE,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
            "Cache-Control": "no-store",
        },
    )


@router.post("/{student_id}/check-in", response_model=AttendanceRowOut)
def check_in(
    student_id: UUID,
    staff: AttendanceOperate,
    db: Db,
    clock: ClockDep,
    body: CheckInIn | None = None,
) -> AttendanceRowOut:
    out = attendance_service.check_in(
        db, student_id, actor=staff, note=body.note if body else None, clock=clock
    )
    db.commit()
    return out


@router.post("/{student_id}/check-out", response_model=AttendanceRowOut)
def check_out(
    student_id: UUID,
    staff: AttendanceOperate,
    db: Db,
    clock: ClockDep,
    body: CheckOutIn | None = None,
) -> AttendanceRowOut:
    out = attendance_service.check_out(
        db, student_id, actor=staff, note=body.note if body else None, clock=clock
    )
    db.commit()
    return out


@router.post("/{student_id}/mark-absent", response_model=AttendanceRowOut)
def mark_absent(
    student_id: UUID,
    staff: AttendanceOperate,
    db: Db,
    clock: ClockDep,
    body: MarkAbsentIn | None = None,
) -> AttendanceRowOut:
    out = attendance_service.mark_absent(
        db, student_id, actor=staff, note=body.note if body else None, clock=clock
    )
    db.commit()
    return out


@router.patch("/{attendance_id}", response_model=AttendanceRowOut)
def amend(
    attendance_id: UUID,
    body: AttendanceAmendIn,
    staff: AttendanceAmend,
    db: Db,
    meta: Annotated[RequestMeta, Depends(get_request_meta)],
    clock: ClockDep,
) -> AttendanceRowOut:
    out = attendance_service.amend_attendance(
        db, attendance_id, body, actor=staff, meta=meta, clock=clock
    )
    db.commit()
    return out
