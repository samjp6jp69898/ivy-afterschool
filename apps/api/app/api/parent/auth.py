"""BACKEND-053：家長端認證 endpoint（``/api/parent/auth/*``）。

- ``POST /liff-login``：``ParentAuthService.liff_login`` →
  已綁定：commit → 設 parent access / refresh cookie、清 bind cookie → ``{"status":"ok","parent":
  ParentMeOut}``（ParentMeOut 由 BACKEND-063 ``get_me`` 組出，含小孩清單）；
  未綁定：設 bind cookie（10 分鐘）→ ``{"status":"needs_binding","parent":null,"name_hint":...}``，
  不建立帳號、不 commit。回應 body 不含任何 token。

BACKEND-059：``POST /refresh``：讀 ``parent_refresh`` cookie（path 限 ``/api/parent/auth``）→
``ParentAuthService.refresh`` → ``get_me`` → commit → 替換家長 access / refresh cookie →
``ParentAuthOut``。401 時回錯誤 envelope 並清除家長 cookie（撤銷已由 service 在 raise 前 commit）；
409 ``refresh_in_progress`` 不清 cookie。

BACKEND-061：``POST /logout``：讀 ``parent_refresh`` → ``ParentAuthService.logout`` → commit →
清除家長 access / refresh 與 bind cookie → ``{"message": "已登出"}``；無 cookie 也 200。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.deps import CurrentParent
from app.core.clock import Clock, get_clock
from app.core.config import Settings, get_settings
from app.core.db import get_db
from app.core.errors import UnauthenticatedError, error_response
from app.core.request_meta import RequestMeta, get_request_meta
from app.core.security.cookies import (
    PARENT_REFRESH,
    clear_auth_cookies,
    clear_bind_cookie,
    read_cookie,
    set_auth_cookies,
    set_bind_cookie,
)
from app.core.storage import Storage, get_storage
from app.schemas.auth import LiffLoginIn, LiffLoginOut, MessageOut, ParentAuthOut
from app.services import parent_account_service
from app.services.auth import parent_auth
from app.services.auth.line_id_token import LineIdTokenVerifier, get_line_verifier
from app.services.auth.parent_auth import NeedsBinding, ParentSession
from app.services.auth.throttle import AuthThrottles, get_auth_throttles

router = APIRouter(prefix="/auth", tags=["parent-auth"])


def current_parent_of(session: ParentSession) -> CurrentParent:
    parent = session.parent
    return CurrentParent(
        id=parent.id,
        line_user_id=parent.line_user_id,
        display_name=parent.display_name,
        token_version=parent.token_version,
    )


@router.post("/liff-login", response_model=LiffLoginOut)
def liff_login(
    body: LiffLoginIn,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    verifier: Annotated[LineIdTokenVerifier, Depends(get_line_verifier)],
    meta: Annotated[RequestMeta, Depends(get_request_meta)],
    throttles: Annotated[AuthThrottles, Depends(get_auth_throttles)],
    clock: Annotated[Clock, Depends(get_clock)],
    settings: Annotated[Settings, Depends(get_settings)],
    storage: Annotated[Storage, Depends(get_storage)],
) -> LiffLoginOut:
    result = parent_auth.liff_login(
        db,
        id_token=body.id_token,
        verifier=verifier,
        meta=meta,
        throttles=throttles,
        clock=clock,
    )
    if isinstance(result, NeedsBinding):
        set_bind_cookie(response, result.bind_token, settings=settings)
        return LiffLoginOut(status="needs_binding", parent=None, name_hint=result.name_hint)

    me = parent_account_service.get_me(
        db, parent=current_parent_of(result), storage=storage, clock=clock
    )
    db.commit()
    set_auth_cookies(
        response,
        subject_type="parent",
        access_token=result.access_token,
        refresh_token=result.refresh_token,
        settings=settings,
    )
    clear_bind_cookie(response, settings=settings)
    return LiffLoginOut(status="ok", parent=me, name_hint=None)


@router.post("/refresh", response_model=ParentAuthOut)
def refresh(
    request: Request,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    clock: Annotated[Clock, Depends(get_clock)],
    settings: Annotated[Settings, Depends(get_settings)],
    storage: Annotated[Storage, Depends(get_storage)],
) -> ParentAuthOut | JSONResponse:
    try:
        result = parent_auth.refresh(
            db, raw_refresh=read_cookie(request, PARENT_REFRESH.name), clock=clock
        )
    except UnauthenticatedError as exc:
        # exception handler 會另建回應，附在 ``response`` 上的 Set-Cookie 不會帶出去，這裡自組
        error = error_response(exc)
        clear_auth_cookies(error, subject_type="parent", settings=settings)
        return error
    me = parent_account_service.get_me(
        db, parent=current_parent_of(result), storage=storage, clock=clock
    )
    db.commit()
    set_auth_cookies(
        response,
        subject_type="parent",
        access_token=result.access_token,
        refresh_token=result.refresh_token,
        settings=settings,
    )
    return ParentAuthOut(parent=me)


@router.post("/logout", response_model=MessageOut)
def logout(
    request: Request,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    clock: Annotated[Clock, Depends(get_clock)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> MessageOut:
    """登出永遠成功：不需有效 access token，無 cookie 或 refresh 不存在也回 200 並清 cookie。"""
    parent_auth.logout(db, raw_refresh=read_cookie(request, PARENT_REFRESH.name), clock=clock)
    db.commit()
    clear_auth_cookies(response, subject_type="parent", settings=settings)
    clear_bind_cookie(response, settings=settings)
    return MessageOut(message="已登出")
