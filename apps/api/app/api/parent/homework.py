"""BACKEND-391：家長端作業 endpoint。

``GET /api/parent/children/{student_id}/homework?date=``：``get_owned_student``（不屬於自己 → 404）
→ BACKEND-384 ``get_child_homework``；date 省略時為台北今天（``clock.today()``）。
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_owned_student
from app.core.clock import Clock, get_clock
from app.core.db import get_db
from app.models.students import Student
from app.schemas.homework import ParentHomeworkOut
from app.services import homework_service

router = APIRouter(tags=["parent-homework"])


@router.get("/children/{student_id}/homework", response_model=ParentHomeworkOut)
def get_child_homework(
    student: Annotated[Student, Depends(get_owned_student)],
    db: Annotated[Session, Depends(get_db)],
    clock: Annotated[Clock, Depends(get_clock)],
    service_date: Annotated[date | None, Query(alias="date")] = None,
) -> ParentHomeworkOut:
    return homework_service.get_child_homework(
        db, student.id, service_date if service_date is not None else clock.today()
    )
