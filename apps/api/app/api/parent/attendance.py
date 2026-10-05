"""BACKEND-323：家長端出勤 endpoint。

``GET /api/parent/children/{student_id}/attendance?month=YYYY-MM``：``get_owned_student``（不屬於
自己 / 不存在 / 封存皆同一 404）→ BACKEND-314 ``get_child_monthly_attendance``；month 省略時為
``clock.today()`` 所在月份。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_owned_student
from app.core.clock import Clock, get_clock
from app.core.db import get_db
from app.models.students import Student
from app.schemas.attendance import MONTH_PATTERN, ParentMonthlyAttendanceOut
from app.services import attendance_service

router = APIRouter(tags=["parent-attendance"])


@router.get("/children/{student_id}/attendance", response_model=ParentMonthlyAttendanceOut)
def get_child_monthly_attendance(
    student: Annotated[Student, Depends(get_owned_student)],
    db: Annotated[Session, Depends(get_db)],
    clock: Annotated[Clock, Depends(get_clock)],
    month: Annotated[str | None, Query(pattern=MONTH_PATTERN)] = None,
) -> ParentMonthlyAttendanceOut:
    return attendance_service.get_child_monthly_attendance(
        db, student.id, month if month is not None else clock.today().strftime("%Y-%m"), clock=clock
    )
