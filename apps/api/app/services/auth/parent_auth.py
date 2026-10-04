"""BACKEND-052：家長 LIFF 登入（ParentAuthService.liff_login）。

移植 ivy ``api/parent_portal/auth.py::liff_login``；去掉 tenant、role 欄位判斷、device setup。

- 節流：同 IP 滑動視窗（``throttles.liff_ip``，BACKEND-040）。
- ``line.liff.channel_id``（BACKEND-106 / 108）為空 → 503 ``line_login_not_configured``，不呼叫
  LINE。
- ``verifier.verify``（BACKEND-051）的 401 / 503 原樣往外拋。
- 已有帳號且 ``status='disabled'`` → 403 ``parent_disabled``（停用的家長不能進入綁定流程重新
  建帳號）。
- 已有帳號、active、且有任何有效綁定（BACKEND-179 ``get_parent_student_ids`` 非空）→ 同步
  display_name / picture_url（LINE 值非空且有變才更新）、``last_login_at = now``、新 family 的
  refresh、access → ``ParentSession``。
- 沒有帳號、或帳號存在但沒有任何有效綁定（監護人 / 學生封存、被解除綁定）→ 發綁定臨時 token
  （BACKEND-034）→ ``NeedsBinding``。**不**建立 parent_accounts，首次綁定成功（BACKEND-055）才建立。
- 只 flush 不 commit（endpoint commit）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import Clock
from app.core.errors import AppError, ForbiddenError
from app.core.request_meta import RequestMeta
from app.core.security.tokens import BindClaims, create_access_token, create_bind_token
from app.core.settings_registry import LINE_LIFF
from app.models.parents import ParentAccount
from app.services.auth import refresh_tokens
from app.services.auth.line_id_token import LineIdTokenVerifier, LineProfile
from app.services.auth.throttle import AuthThrottles
from app.services.parent_scope import get_parent_student_ids
from app.services.settings_service import get_setting

_UNKNOWN_IP: Final = "-"


@dataclass(frozen=True)
class ParentSession:
    parent: ParentAccount
    access_token: str
    refresh_token: str


@dataclass(frozen=True)
class NeedsBinding:
    bind_token: str
    name_hint: str | None


def liff_login(
    session: Session,
    *,
    id_token: str,
    verifier: LineIdTokenVerifier,
    meta: RequestMeta,
    throttles: AuthThrottles,
    clock: Clock,
) -> ParentSession | NeedsBinding:
    now = clock.now()
    throttles.liff_ip.hit(meta.ip or _UNKNOWN_IP, now)

    channel_id = get_setting(session, LINE_LIFF).channel_id
    if not channel_id:
        raise AppError("line_login_not_configured", "LINE 登入尚未設定", status=503)

    profile = verifier.verify(id_token, channel_id=channel_id)

    parent = session.execute(
        select(ParentAccount).where(ParentAccount.line_user_id == profile.line_user_id)
    ).scalar_one_or_none()
    if parent is not None:
        if parent.status == "disabled":
            raise ForbiddenError("此帳號已停用，請洽安親班", code="parent_disabled")
        if get_parent_student_ids(session, parent.id):
            return _login_bound_parent(session, parent, profile=profile, clock=clock)

    bind_token = create_bind_token(
        BindClaims(
            line_user_id=profile.line_user_id,
            display_name=profile.display_name,
            picture_url=profile.picture_url,
        ),
        clock=clock,
    )
    return NeedsBinding(bind_token=bind_token, name_hint=profile.display_name)


def _login_bound_parent(
    session: Session, parent: ParentAccount, *, profile: LineProfile, clock: Clock
) -> ParentSession:
    if profile.display_name and parent.display_name != profile.display_name:
        parent.display_name = profile.display_name
    if profile.picture_url and parent.picture_url != profile.picture_url:
        parent.picture_url = profile.picture_url
    parent.last_login_at = clock.now()
    issued = refresh_tokens.issue(session, subject_type="parent", subject_id=parent.id, clock=clock)
    access_token = create_access_token(
        subject_type="parent",
        subject_id=parent.id,
        token_version=parent.token_version,
        clock=clock,
    )
    session.flush()
    return ParentSession(parent=parent, access_token=access_token, refresh_token=issued.raw)
