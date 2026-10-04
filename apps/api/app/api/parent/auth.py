"""BACKEND-053：家長端認證 endpoint（``/api/parent/auth/*``）。

- ``POST /liff-login``：``ParentAuthService.liff_login`` →
  已綁定：commit → 設 parent access / refresh cookie、清 bind cookie → ``{"status":"ok","parent":
  ParentMeOut}``（ParentMeOut 由 BACKEND-063 ``get_me`` 組出，含小孩清單）；
  未綁定：設 bind cookie（10 分鐘）→ ``{"status":"needs_binding","parent":null,"name_hint":...}``，
  不建立帳號、不 commit。回應 body 不含任何 token。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.api.deps import CurrentParent
from app.core.clock import Clock, get_clock
from app.core.config import Settings, get_settings
from app.core.db import get_db
from app.core.request_meta import RequestMeta, get_request_meta
from app.core.security.cookies import clear_bind_cookie, set_auth_cookies, set_bind_cookie
from app.core.storage import Storage, get_storage
from app.schemas.auth import LiffLoginIn, LiffLoginOut
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
