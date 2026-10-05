"""BACKEND-184：家長端小孩 endpoint。

``GET /api/parent/children``：自己的小孩清單。營運模組的 ``/children/{student_id}/...`` 路由放各自
模組檔，並一律使用 ``get_owned_student`` / ``get_owned_student_for_write``（不屬於自己 → 404）。
``GET /api/parent/children/{student_id}``（BACKEND-185）：自己的小孩詳情（BACKEND-183 ``get_child``
內含 IDOR 檢查：他人、不存在、封存學生皆同一個 404 ``student_not_found``）。
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import CurrentParent, get_current_parent
from app.core.db import get_db
from app.core.storage import Storage, get_storage
from app.schemas.parent_children import ChildDetailOut, ChildSummaryOut
from app.services import parent_children_service

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
