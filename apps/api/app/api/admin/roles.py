"""BACKEND-081 / 082 / 084 / 085：後台角色 endpoint。

- ``GET /api/admin/roles``：roles:read 或 staff:read（員工帳號頁的角色選單也需要讀角色）。
- ``POST /api/admin/roles``：roles:write；``RoleCreateIn`` → ``create_role`` → commit → 201
  ``RoleOut``。422（格式 / 多餘欄位 / 未知權限碼）、403（缺權限或授出超出自身的碼）、409（code
  重複）由 schema / service 拋出。
- ``DELETE /api/admin/roles/{role_id}``：roles:write；系統角色 / 使用中 409，commit 後 204。
- ``GET /api/admin/permissions``：roles:read 或 staff:read；權限碼目錄（純記憶體資料）。
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.api.deps import CurrentStaff, require_any_permission, require_permission
from app.core.db import get_db
from app.core.permissions import PERMISSION_GROUPS, PERMISSION_LABELS, Permission
from app.core.request_meta import RequestMeta, get_request_meta
from app.schemas.roles import (
    PermissionCatalogOut,
    PermissionGroupOut,
    PermissionItemOut,
    RoleCreateIn,
    RoleOut,
)
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


@router.post("/roles", status_code=201, response_model=RoleOut)
def create_role(
    body: RoleCreateIn,
    staff: Annotated[CurrentStaff, Depends(require_permission(Permission.ROLES_WRITE))],
    db: Annotated[Session, Depends(get_db)],
    meta: Annotated[RequestMeta, Depends(get_request_meta)],
) -> RoleOut:
    out = role_service.create_role(db, body, actor=staff, meta=meta)
    db.commit()
    return out


@router.delete("/roles/{role_id}", status_code=204, response_class=Response)
def delete_role(
    role_id: UUID,
    staff: Annotated[CurrentStaff, Depends(require_permission(Permission.ROLES_WRITE))],
    db: Annotated[Session, Depends(get_db)],
    meta: Annotated[RequestMeta, Depends(get_request_meta)],
) -> Response:
    role_service.delete_role(db, role_id, actor=staff, meta=meta)
    db.commit()
    return Response(status_code=204)


@router.get("/permissions", response_model=PermissionCatalogOut)
def permission_catalog(
    _: Annotated[
        CurrentStaff, Depends(require_any_permission(Permission.ROLES_READ, Permission.STAFF_READ))
    ],
) -> PermissionCatalogOut:
    return PermissionCatalogOut(
        groups=[
            PermissionGroupOut(
                key=group.key,
                label=group.label,
                permissions=[
                    PermissionItemOut(code=str(p), label=PERMISSION_LABELS[p])
                    for p in group.permissions
                ],
            )
            for group in PERMISSION_GROUPS
        ]
    )
