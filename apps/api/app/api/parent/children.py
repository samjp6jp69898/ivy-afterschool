"""BACKEND-184：家長端小孩 endpoint。

``GET /api/parent/children``：自己的小孩清單。營運模組的 ``/children/{student_id}/...`` 路由放各自
模組檔，並一律使用 ``get_owned_student`` / ``get_owned_student_for_write``（不屬於自己 → 404）。
``GET /api/parent/children/{student_id}``（BACKEND-185）：自己的小孩詳情（BACKEND-183 ``get_child``
內含 IDOR 檢查：他人、不存在、封存學生皆同一個 404 ``student_not_found``）。
``GET /api/parent/children/{student_id}/today``（BACKEND-526）：``get_owned_student`` → BACKEND-525
``get_child_today``（今日狀態卡；之後的即時變化由 ws 事件更新，斷線重連時再呼叫補齊）。
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import CurrentParent, get_current_parent, get_owned_student
from app.core.clock import Clock, get_clock
from app.core.db import get_db
from app.core.storage import Storage, get_storage
from app.models.students import Student
from app.schemas.parent_children import ChildDetailOut, ChildSummaryOut, ChildTodayOut
from app.services import parent_children_service, parent_today_service

router = APIRouter(prefix="/children", tags=["parent-children"])


@router.get("", response_model=list[ChildSummaryOut])
def list_children(
    parent: Annotated[CurrentParent, Depends(get_current_parent)],
    db: Annotated[Session, Depends(get_db)],
    storage: Annotated[Storage, Depends(get_storage)],
) -> list[ChildSummaryOut]:
    return parent_children_service.list_children(db, parent.id, storage=storage)


@router.get("/{student_id}", response_model=ChildDetailOut)
def get_child(
    student_id: UUID,
    parent: Annotated[CurrentParent, Depends(get_current_parent)],
    db: Annotated[Session, Depends(get_db)],
    storage: Annotated[Storage, Depends(get_storage)],
) -> ChildDetailOut:
    return parent_children_service.get_child(db, parent.id, student_id, storage=storage)


@router.get("/{student_id}/today", response_model=ChildTodayOut)
def get_child_today(
    student: Annotated[Student, Depends(get_owned_student)],
    db: Annotated[Session, Depends(get_db)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> ChildTodayOut:
    return parent_today_service.get_child_today(db, student.id, clock=clock)
