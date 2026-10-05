"""後台員工帳號 endpoint。

- BACKEND-093 ``GET /staff-users``：staff:read；``StaffUserListQuery``（q / role_id / is_active）+
  分頁 → BACKEND-087 ``list_staff_users`` → ``Page[StaffUserOut]``（不含 password_hash）。
- BACKEND-094 ``POST /staff-users``：staff:write；``StaffUserCreateIn`` → BACKEND-089
  ``create_staff_user``（角色不存在 422、超出自身權限 403、帳號重複 409）→ commit → 201
  ``StaffUserCreatedOut``。臨時密碼只在此回傳一次，回應加 ``Cache-Control: no-store``。
- BACKEND-095 ``GET /staff-users/{staff_id}``：staff:read → BACKEND-088 ``get_staff_user`` →
  ``StaffUserOut``；不存在 404 ``staff_user_not_found``。
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.api.admin._query import query_model
from app.api.deps import CurrentStaff, require_permission
from app.core.db import get_db
from app.core.pagination import Page, PageParams, page_params
from app.core.permissions import Permission
from app.core.request_meta import RequestMeta, get_request_meta
from app.schemas.staff_users import (
    StaffUserCreatedOut,
    StaffUserCreateIn,
    StaffUserListQuery,
    StaffUserOut,
)
from app.services import staff_user_service

router = APIRouter(prefix="/staff-users", tags=["admin-staff-users"])


@router.get("", response_model=Page[StaffUserOut])
def list_staff_users(
    _: Annotated[CurrentStaff, Depends(require_permission(Permission.STAFF_READ))],
    query: Annotated[StaffUserListQuery, Depends(query_model(StaffUserListQuery))],
    page: Annotated[PageParams, Depends(page_params)],
    db: Annotated[Session, Depends(get_db)],
) -> Page[StaffUserOut]:
    return staff_user_service.list_staff_users(db, query, page)


@router.post("", status_code=201, response_model=StaffUserCreatedOut)
def create_staff_user(
    body: StaffUserCreateIn,
    response: Response,
    staff: Annotated[CurrentStaff, Depends(require_permission(Permission.STAFF_WRITE))],
    db: Annotated[Session, Depends(get_db)],
    meta: Annotated[RequestMeta, Depends(get_request_meta)],
) -> StaffUserCreatedOut:
    out = staff_user_service.create_staff_user(db, body, actor=staff, meta=meta)
    db.commit()
    # 臨時密碼只回這一次：瀏覽器與中介不得快取
    response.headers["Cache-Control"] = "no-store"
    return out


@router.get("/{staff_id}", response_model=StaffUserOut)
def get_staff_user(
    staff_id: UUID,
    _: Annotated[CurrentStaff, Depends(require_permission(Permission.STAFF_READ))],
    db: Annotated[Session, Depends(get_db)],
) -> StaffUserOut:
    return staff_user_service.get_staff_user(db, staff_id)
