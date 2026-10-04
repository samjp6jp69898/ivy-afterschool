"""BACKEND-141：後台班級 endpoint。

``GET /api/admin/classes``：classes:read；``mine=true`` 只列目前員工負責的班。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import CurrentStaff, require_permission
from app.core.db import get_db
from app.core.permissions import Permission
from app.schemas.classes import ClassListQuery, ClassOut
from app.services import class_service

router = APIRouter(prefix="/classes", tags=["admin-classes"])


@router.get("", response_model=list[ClassOut])
def list_classes(
    staff: Annotated[CurrentStaff, Depends(require_permission(Permission.CLASSES_READ))],
    query: Annotated[ClassListQuery, Query()],
    db: Annotated[Session, Depends(get_db)],
) -> list[ClassOut]:
    return class_service.list_classes(db, query, actor=staff)
