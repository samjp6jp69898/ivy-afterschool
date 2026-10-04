"""BACKEND-042：後台認證 endpoint（``/api/admin/auth/*``，不掛權限守衛，見 route_audit 白名單）。

- ``POST /login``：``StaffAuthService.login`` → commit → 設 httpOnly cookie → ``StaffAuthOut``。
  失敗時 DB 無寫入（失敗計數在記憶體），不需 commit。回應 body 不含任何 token。
- ``staff_auth_out``：login / refresh / change-password 共用的回應組裝（permissions 先排序，
  ``StaffMeOut`` 不自行排序）。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import CurrentStaff, get_current_staff
from app.core.clock import Clock, get_clock
from app.core.config import Settings, get_settings
from app.core.db import get_db
from app.core.request_meta import RequestMeta, get_request_meta
from app.core.security.cookies import (
    STAFF_REFRESH,
    clear_auth_cookies,
    read_cookie,
    set_auth_cookies,
)
from app.schemas.auth import MessageOut, RoleBrief, StaffAuthOut, StaffLoginIn, StaffMeOut
from app.services.auth import staff_auth
from app.services.auth.staff_auth import StaffSession
from app.services.auth.throttle import AuthThrottles, get_auth_throttles

router = APIRouter(prefix="/auth", tags=["admin-auth"])


def staff_auth_out(session: StaffSession) -> StaffAuthOut:
    staff = session.staff
    return StaffAuthOut(
        user=StaffMeOut(
            id=staff.id,
            username=staff.username,
            display_name=staff.display_name,
            role=RoleBrief(id=staff.role.id, code=staff.role.code, name=staff.role.name),
            permissions=sorted(session.permissions),
            must_change_password=staff.must_change_password,
        )
    )


@router.post("/login", response_model=StaffAuthOut)
def login(
    body: StaffLoginIn,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    meta: Annotated[RequestMeta, Depends(get_request_meta)],
    throttles: Annotated[AuthThrottles, Depends(get_auth_throttles)],
    clock: Annotated[Clock, Depends(get_clock)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> StaffAuthOut:
    result = staff_auth.login(
        db,
        username=body.username,
        password=body.password,
        meta=meta,
        throttles=throttles,
        clock=clock,
    )
    out = staff_auth_out(result)
    db.commit()
    set_auth_cookies(
        response,
        subject_type="staff",
        access_token=result.access_token,
        refresh_token=result.refresh_token,
        settings=settings,
    )
    return out


@router.post("/logout", response_model=MessageOut)
def logout(
    request: Request,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    clock: Annotated[Clock, Depends(get_clock)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> MessageOut:
    """登出永遠成功：不需有效 access token，無 cookie 或 refresh 不存在也回 200 並清 cookie。"""
    staff_auth.logout(db, raw_refresh=read_cookie(request, STAFF_REFRESH.name), clock=clock)
    db.commit()
    clear_auth_cookies(response, subject_type="staff", settings=settings)
    return MessageOut(message="已登出")


@router.get("/me", response_model=StaffMeOut)
def me(staff: Annotated[CurrentStaff, Depends(get_current_staff)]) -> StaffMeOut:
    """目前登入員工（前端以此建立權限選單）；本路徑在強制改密碼的 allowlist 內。"""
    return StaffMeOut(
        id=staff.id,
        username=staff.username,
        display_name=staff.display_name,
        role=RoleBrief(id=staff.role_id, code=staff.role_code, name=staff.role_name),
        permissions=sorted(staff.permissions),
        must_change_password=staff.must_change_password,
    )
