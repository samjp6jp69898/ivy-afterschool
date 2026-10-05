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

BACKEND-057：``POST /bind``（``BindIn``）：身分先試 ``parent_access`` cookie
（``get_optional_parent``，無效回 None）→ 有效即為加綁；否則讀 ``parent_bind`` cookie 並
``decode_bind_token`` → 首次；兩者皆無或皆無效 → 401 ``unauthenticated``。``ParentAuthService.bind``
（BACKEND-056）→ ``get_me`` → commit → 首次：設家長 access / refresh cookie、清 bind cookie；加綁：
cookie 不動 → ``ParentAuthOut``。綁定碼錯誤由 service 拋 400 / 409，請求 rollback 讓碼不被消耗。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.deps import CurrentParent, get_optional_parent
from app.core.clock import Clock, get_clock
from app.core.config import Settings, get_settings
from app.core.db import get_db
from app.core.errors import UnauthenticatedError, error_response
from app.core.request_meta import RequestMeta, get_request_meta
from app.core.security.cookies import (
    PARENT_BIND,
    PARENT_REFRESH,
    clear_auth_cookies,
    clear_bind_cookie,
    read_cookie,
    set_auth_cookies,
    set_bind_cookie,
)
from app.core.security.tokens import BindClaims, decode_bind_token
from app.core.storage import Storage, get_storage
from app.models.parents import ParentAccount
from app.schemas.auth import BindIn, LiffLoginIn, LiffLoginOut, MessageOut, ParentAuthOut
from app.services import parent_account_service
from app.services.auth import parent_auth
from app.services.auth.line_id_token import LineIdTokenVerifier, get_line_verifier
from app.services.auth.parent_auth import NeedsBinding, ParentSession
from app.services.auth.throttle import AuthThrottles, get_auth_throttles

router = APIRouter(prefix="/auth", tags=["parent-auth"])


def current_parent_of(session: ParentSession) -> CurrentParent:
    return _current_parent(session.parent)


def _current_parent(parent: ParentAccount) -> CurrentParent:
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


@router.post("/bind", response_model=ParentAuthOut)
def bind(
    body: BindIn,
    request: Request,
    response: Response,
    parent: Annotated[CurrentParent | None, Depends(get_optional_parent)],
    db: Annotated[Session, Depends(get_db)],
    throttles: Annotated[AuthThrottles, Depends(get_auth_throttles)],
    clock: Annotated[Clock, Depends(get_clock)],
    settings: Annotated[Settings, Depends(get_settings)],
    storage: Annotated[Storage, Depends(get_storage)],
) -> ParentAuthOut:
    identity: CurrentParent | BindClaims
    if parent is not None:
        identity = parent
    else:
        raw_bind = read_cookie(request, PARENT_BIND.name)
        if not raw_bind:
            raise UnauthenticatedError
        identity = decode_bind_token(raw_bind, clock=clock)

    result = parent_auth.bind(
        db, raw_code=body.code, identity=identity, throttles=throttles, clock=clock
    )
    me = parent_account_service.get_me(
        db, parent=_current_parent(result.parent), storage=storage, clock=clock
    )
    db.commit()
    if result.session_tokens is not None:
        set_auth_cookies(
            response,
            subject_type="parent",
            access_token=result.session_tokens.access_token,
            refresh_token=result.session_tokens.refresh_token,
            settings=settings,
        )
        clear_bind_cookie(response, settings=settings)
    return ParentAuthOut(parent=me)
