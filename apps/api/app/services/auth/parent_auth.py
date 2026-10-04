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

BACKEND-058：``refresh``（與 BACKEND-043 對稱）。無 cookie → 401；``rotate`` 的錯誤原樣往外拋；
輪替出的 token 不是 parent → 撤銷 family、401 ``refresh_invalid``；家長不存在或
``status='disabled'`` → 撤銷 family、401 ``unauthenticated``。兩個 401 撤銷路徑都**在 raise 前
commit**（endpoint 的 401 rollback 不會退回撤銷，service 不 commit 原則的例外見 BACKEND-037）。
以目前 ``token_version`` 簽 access；沒有任何有效綁定的家長仍可 refresh（看到空清單後前往加綁頁）。

BACKEND-060：``logout``（同 BACKEND-045）：無 cookie / 不存在 → 0，否則撤銷整個 family 回撤銷筆數。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import Clock
from app.core.errors import AppError, ForbiddenError, UnauthenticatedError
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


def _parent_session(parent: ParentAccount, *, refresh_token: str, clock: Clock) -> ParentSession:
    access_token = create_access_token(
        subject_type="parent",
        subject_id=parent.id,
        token_version=parent.token_version,
        clock=clock,
    )
    return ParentSession(parent=parent, access_token=access_token, refresh_token=refresh_token)


def _login_bound_parent(
    session: Session, parent: ParentAccount, *, profile: LineProfile, clock: Clock
) -> ParentSession:
    if profile.display_name and parent.display_name != profile.display_name:
        parent.display_name = profile.display_name
    if profile.picture_url and parent.picture_url != profile.picture_url:
        parent.picture_url = profile.picture_url
    parent.last_login_at = clock.now()
    issued = refresh_tokens.issue(session, subject_type="parent", subject_id=parent.id, clock=clock)
    result = _parent_session(parent, refresh_token=issued.raw, clock=clock)
    session.flush()
    return result


def _revoke_family_and_commit(session: Session, raw: str, clock: Clock) -> None:
    """撤銷 raw 所屬 family 並 commit：呼叫端接著 raise，endpoint 的 rollback 不會退回撤銷。"""
    refresh_tokens.revoke_family_by_raw(session, raw, clock=clock)
    session.commit()


def refresh(session: Session, *, raw_refresh: str | None, clock: Clock) -> ParentSession:
    if not raw_refresh:
        raise UnauthenticatedError
    rotated = refresh_tokens.rotate(session, raw_refresh, clock=clock)
    if rotated.subject_type != "parent":
        _revoke_family_and_commit(session, rotated.raw, clock)
        raise UnauthenticatedError(code="refresh_invalid")
    parent = session.execute(
        select(ParentAccount).where(ParentAccount.id == rotated.subject_id)
    ).scalar_one_or_none()
    if parent is None or parent.status == "disabled":
        _revoke_family_and_commit(session, rotated.raw, clock)
        raise UnauthenticatedError
    result = _parent_session(parent, refresh_token=rotated.raw, clock=clock)
    session.flush()
    return result


def logout(session: Session, *, raw_refresh: str | None, clock: Clock) -> int:
    if not raw_refresh:
        return 0
    return refresh_tokens.revoke_family_by_raw(session, raw_refresh, clock=clock)
