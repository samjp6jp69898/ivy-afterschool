"""BACKEND-141：後台班級 endpoint。

``GET /api/admin/classes``：classes:read；``mine=true`` 只列目前員工負責的班。
``POST /api/admin/classes``（BACKEND-142）：classes:write → BACKEND-137 ``create_class`` → 201。
``GET /api/admin/classes/{class_id}``（BACKEND-143）：classes:read → BACKEND-136 ``get_class``
（封存班也可查）。
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import CurrentStaff, require_permission
from app.core.db import get_db
from app.core.permissions import Permission
from app.schemas.classes import ClassCreateIn, ClassListQuery, ClassOut
from app.services import class_service

router = APIRouter(prefix="/classes", tags=["admin-classes"])


@router.get("", response_model=list[ClassOut])
def list_classes(
    staff: Annotated[CurrentStaff, Depends(require_permission(Permission.CLASSES_READ))],
    query: Annotated[ClassListQuery, Query()],
    db: Annotated[Session, Depends(get_db)],
) -> list[ClassOut]:
    return class_service.list_classes(db, query, actor=staff)


@router.post("", response_model=ClassOut, status_code=201)
def create_class(
    body: ClassCreateIn,
    _: Annotated[CurrentStaff, Depends(require_permission(Permission.CLASSES_WRITE))],
    db: Annotated[Session, Depends(get_db)],
) -> ClassOut:
    out = class_service.create_class(db, body)
    db.commit()
    return out


@router.get("/{class_id}", response_model=ClassOut)
def get_class(
    class_id: UUID,
    _: Annotated[CurrentStaff, Depends(require_permission(Permission.CLASSES_READ))],
    db: Annotated[Session, Depends(get_db)],
) -> ClassOut:
    return class_service.get_class(db, class_id)
