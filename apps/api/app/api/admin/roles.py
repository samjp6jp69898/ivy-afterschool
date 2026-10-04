"""BACKEND-081：後台角色 endpoint（與 BACKEND-085 同檔）。

``GET /api/admin/roles``：roles:read 或 staff:read（員工帳號頁的角色選單也需要讀角色）。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import CurrentStaff, require_any_permission
from app.core.db import get_db
from app.core.permissions import Permission
from app.schemas.roles import RoleOut
from app.services import role_service

router = APIRouter(tags=["admin-roles"])


@router.get("/roles", response_model=list[RoleOut])
def list_roles(
    _: Annotated[
        CurrentStaff, Depends(require_any_permission(Permission.ROLES_READ, Permission.STAFF_READ))
    ],
    db: Annotated[Session, Depends(get_db)],
) -> list[RoleOut]:
    return role_service.list_roles(db)
