"""BACKEND-042：後台認證 endpoint（``/api/admin/auth/*``，不掛權限守衛，見 route_audit 白名單）。

- ``POST /login``：``StaffAuthService.login`` → commit → 設 httpOnly cookie → ``StaffAuthOut``。
  失敗時 DB 無寫入（失敗計數在記憶體），不需 commit。回應 body 不含任何 token。
- ``staff_auth_out``：login / refresh / change-password 共用的回應組裝（permissions 先排序，
  ``StaffMeOut`` 不自行排序）。

BACKEND-044：``POST /refresh``：從 ``staff_refresh`` cookie（path 限 ``/api/admin/auth``）取 raw →
``StaffAuthService.refresh`` → commit → 替換兩個 cookie → ``StaffAuthOut``。不需 access token、無
request body。401 時回錯誤 envelope 並清除員工 cookie（family 撤銷已由 service 在 raise 前
commit，這裡不再 commit）；409 ``refresh_in_progress`` 不清 cookie（併發重打即可）。

BACKEND-050：``POST /change-password``：``get_current_staff``（在強制改密碼 allowlist 內）→
``StaffAuthService.change_password`` → commit → 設新 access（新 token_version）與新 family 的
refresh → ``StaffAuthOut``（``must_change_password=false``）。400 / 422 / 429 由 service 拋出、
exception handler 回應，cookie 不動。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.deps import CurrentStaff, get_current_staff
from app.core.clock import Clock, get_clock
from app.core.config import Settings, get_settings
from app.core.db import get_db
from app.core.errors import UnauthenticatedError, error_response
from app.core.request_meta import RequestMeta, get_request_meta
from app.core.security.cookies import (
    STAFF_REFRESH,
    clear_auth_cookies,
    read_cookie,
    set_auth_cookies,
)
from app.schemas.auth import (
    ChangePasswordIn,
    MessageOut,
    RoleBrief,
    StaffAuthOut,
    StaffLoginIn,
    StaffMeOut,
)
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


@router.post("/refresh", response_model=StaffAuthOut)
def refresh(
    request: Request,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    clock: Annotated[Clock, Depends(get_clock)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> StaffAuthOut | JSONResponse:
    try:
        result = staff_auth.refresh(
            db, raw_refresh=read_cookie(request, STAFF_REFRESH.name), clock=clock
        )
    except UnauthenticatedError as exc:
        # exception handler 會另建回應，附在 ``response`` 上的 Set-Cookie 不會帶出去，這裡自組
        error = error_response(exc)
        clear_auth_cookies(error, subject_type="staff", settings=settings)
        return error
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


@router.post("/change-password", response_model=StaffAuthOut)
def change_password(
    body: ChangePasswordIn,
    response: Response,
    staff: Annotated[CurrentStaff, Depends(get_current_staff)],
    db: Annotated[Session, Depends(get_db)],
    throttles: Annotated[AuthThrottles, Depends(get_auth_throttles)],
    clock: Annotated[Clock, Depends(get_clock)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> StaffAuthOut:
    result = staff_auth.change_password(
        db,
        staff=staff,
        current_password=body.current_password,
        new_password=body.new_password,
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
