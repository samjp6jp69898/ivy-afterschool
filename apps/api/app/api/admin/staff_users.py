"""後台員工帳號 endpoint。

- BACKEND-093 ``GET /staff-users``：staff:read；``StaffUserListQuery``（q / role_id / is_active）+
  分頁 → BACKEND-087 ``list_staff_users`` → ``Page[StaffUserOut]``（不含 password_hash）。
- BACKEND-094 ``POST /staff-users``：staff:write；``StaffUserCreateIn`` → BACKEND-089
  ``create_staff_user``（角色不存在 422、超出自身權限 403、帳號重複 409）→ commit → 201
  ``StaffUserCreatedOut``。臨時密碼只在此回傳一次，回應加 ``Cache-Control: no-store``。
- BACKEND-095 ``GET /staff-users/{staff_id}``：staff:read → BACKEND-088 ``get_staff_user`` →
  ``StaffUserOut``；不存在 404 ``staff_user_not_found``。
- BACKEND-096 ``PATCH /staff-users/{staff_id}``：staff:write；``StaffUserUpdateIn`` → BACKEND-090
  ``update_staff_user`` → commit → ``StaffUserOut``。
- BACKEND-097 ``POST /staff-users/{staff_id}/reset-password``：staff:write → BACKEND-091
  ``reset_password`` → commit → ``TempPasswordOut``（no-store）；目標既有登入立即失效。
- BACKEND-098 ``POST /staff-users/{staff_id}/deactivate``：staff:write → BACKEND-092 ``deactivate``
  → commit → ``StaffUserOut``；目標既有登入立即失效。
- BACKEND-522 ``POST /staff-users/{staff_id}/activate``：staff:write；無 body + request meta →
  BACKEND-521 ``activate``（權限較大者 403 ``cannot_manage_staff``、已啟用 409
  ``staff_already_active``）→ commit → ``StaffUserCreatedOut``。臨時密碼只在此回傳一次（no-store），
  下次登入須先改密碼。
- BACKEND-528 ``GET /staff-users/options``：classes:write 或 staff:read → BACKEND-527
  ``list_staff_options`` → ``list[StaffOptionOut]``（只含啟用員工）。必須註冊在
  ``GET /staff-users/{staff_id}`` 之前，否則 ``options`` 會被當成 staff_id 而回 422。
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.api.admin._query import query_model
from app.api.deps import CurrentStaff, require_any_permission, require_permission
from app.core.clock import Clock, get_clock
from app.core.db import get_db
from app.core.pagination import Page, PageParams, page_params
from app.core.permissions import Permission
from app.core.request_meta import RequestMeta, get_request_meta
from app.schemas.staff_users import (
    StaffOptionOut,
    StaffUserCreatedOut,
    StaffUserCreateIn,
    StaffUserListQuery,
    StaffUserOut,
    StaffUserUpdateIn,
    TempPasswordOut,
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


@router.get("/options", response_model=list[StaffOptionOut])
def list_staff_options(
    _: Annotated[
        CurrentStaff,
        Depends(require_any_permission(Permission.CLASSES_WRITE, Permission.STAFF_READ)),
    ],
    db: Annotated[Session, Depends(get_db)],
) -> list[StaffOptionOut]:
    return staff_user_service.list_staff_options(db)


@router.get("/{staff_id}", response_model=StaffUserOut)
def get_staff_user(
    staff_id: UUID,
    _: Annotated[CurrentStaff, Depends(require_permission(Permission.STAFF_READ))],
    db: Annotated[Session, Depends(get_db)],
) -> StaffUserOut:
    return staff_user_service.get_staff_user(db, staff_id)


@router.patch("/{staff_id}", response_model=StaffUserOut)
def update_staff_user(
    staff_id: UUID,
    body: StaffUserUpdateIn,
    staff: Annotated[CurrentStaff, Depends(require_permission(Permission.STAFF_WRITE))],
    db: Annotated[Session, Depends(get_db)],
    meta: Annotated[RequestMeta, Depends(get_request_meta)],
) -> StaffUserOut:
    out = staff_user_service.update_staff_user(db, staff_id, body, actor=staff, meta=meta)
    db.commit()
    return out


@router.post("/{staff_id}/reset-password", response_model=TempPasswordOut)
def reset_staff_password(
    staff_id: UUID,
    response: Response,
    staff: Annotated[CurrentStaff, Depends(require_permission(Permission.STAFF_WRITE))],
    db: Annotated[Session, Depends(get_db)],
    meta: Annotated[RequestMeta, Depends(get_request_meta)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> TempPasswordOut:
    out = staff_user_service.reset_password(db, staff_id, actor=staff, meta=meta, clock=clock)
    db.commit()
    # 臨時密碼只回這一次：瀏覽器與中介不得快取
    response.headers["Cache-Control"] = "no-store"
    return out


@router.post("/{staff_id}/deactivate", response_model=StaffUserOut)
def deactivate_staff_user(
    staff_id: UUID,
    staff: Annotated[CurrentStaff, Depends(require_permission(Permission.STAFF_WRITE))],
    db: Annotated[Session, Depends(get_db)],
    meta: Annotated[RequestMeta, Depends(get_request_meta)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> StaffUserOut:
    out = staff_user_service.deactivate(db, staff_id, actor=staff, meta=meta, clock=clock)
    db.commit()
    return out


@router.post("/{staff_id}/activate", response_model=StaffUserCreatedOut)
def activate_staff_user(
    staff_id: UUID,
    response: Response,
    staff: Annotated[CurrentStaff, Depends(require_permission(Permission.STAFF_WRITE))],
    db: Annotated[Session, Depends(get_db)],
    meta: Annotated[RequestMeta, Depends(get_request_meta)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> StaffUserCreatedOut:
    out = staff_user_service.activate(db, staff_id, actor=staff, meta=meta, clock=clock)
    db.commit()
    # 臨時密碼只回這一次：瀏覽器與中介不得快取
    response.headers["Cache-Control"] = "no-store"
    return out
