"""BACKEND-093：後台員工帳號 endpoint。

``GET /api/admin/staff-users``：staff:read；``StaffUserListQuery``（q / role_id / is_active）+ 分頁
→ BACKEND-087 ``list_staff_users`` → ``Page[StaffUserOut]``（不含 password_hash）。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.admin._query import query_model
from app.api.deps import CurrentStaff, require_permission
from app.core.db import get_db
from app.core.pagination import Page, PageParams, page_params
from app.core.permissions import Permission
from app.schemas.staff_users import StaffUserListQuery, StaffUserOut
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
