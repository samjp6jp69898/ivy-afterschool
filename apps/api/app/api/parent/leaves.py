"""BACKEND-355：家長端請假 endpoint。

``GET /api/parent/children/{student_id}/leaves``：``get_owned_student``（不屬於自己 / 不存在 / 封存
皆同一 404）→ BACKEND-348 ``list_child_leaves``（含已取消、附件短效 URL、can_cancel）。
寫入型 handler 呼叫 service 後自行 ``db.commit()``。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_owned_student
from app.core.clock import Clock, get_clock
from app.core.db import get_db
from app.core.pagination import Page, PageParams, page_params
from app.core.storage import Storage, get_storage
from app.models.students import Student
from app.schemas.leaves import ParentLeaveOut
from app.services import leave_service

router = APIRouter(tags=["parent-leaves"])


@router.get("/children/{student_id}/leaves", response_model=Page[ParentLeaveOut])
def list_child_leaves(
    student: Annotated[Student, Depends(get_owned_student)],
    page: Annotated[PageParams, Depends(page_params)],
    db: Annotated[Session, Depends(get_db)],
    storage: Annotated[Storage, Depends(get_storage)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> Page[ParentLeaveOut]:
    return leave_service.list_child_leaves(db, student.id, page, storage=storage, clock=clock)
