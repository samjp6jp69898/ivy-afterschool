"""BACKEND-041：員工帳密登入（StaffAuthService.login）。

移植 ivy ``api/auth.py::login`` 的節流順序、dummy hash 防帳號列舉、成功清除失敗計數；
去掉 tenant、school wifi gate、impersonation、employee 連動。

- 節流順序：IP 滑動視窗（不分成敗）→ (帳號, IP) 鎖定 → 帳號跨來源鎖定；鎖定中回 429。
- 帳號不存在、密碼錯誤、帳號停用一律同一個 401 ``invalid_credentials``、同一句訊息；不存在時對假
  hash 跑一次 argon2、停用時仍先驗密碼，讓三種情況的回應時間一致（不洩漏帳號是否存在 / 停用）。
- 失敗同時計入 (帳號, IP) 與帳號兩個鎖定桶；成功清除兩者。
- 成功：``needs_rehash`` 時透明升級 hash、``last_login_at = now``、簽發 access token（tv =
  staff.token_version）與新 family 的 refresh token；有效權限以 BACKEND-072 計算。
- 只 flush 不 commit（endpoint commit）。``must_change_password`` 仍可登入，由 BACKEND-047 限制
  可用路徑。

BACKEND-043：``refresh``（移植 ivy ``api/auth.py::refresh_token`` 的 staff rotation 分支；去掉 JWT
grace fallback 與 absolute lifetime）。

- 無 cookie → 401 ``unauthenticated``；``rotate``（BACKEND-037）的錯誤（invalid / revoked /
  expired / reused / in_progress）原樣往外拋。
- 輪替出的 token 不是 staff（家長 refresh token 不得換員工 access）→ 撤銷該 family，401
  ``refresh_invalid``；帳號不存在或停用 → 撤銷該 family，401 ``unauthenticated``。
  這兩個撤銷與 ``rotate`` 的重用偵測一樣**在 raise 前 commit**：endpoint（BACKEND-044）只在成功時
  commit，401 路徑會 rollback，不先 commit 撤銷就會跟著退回而失效。
- 成功：以帳號**目前**的 ``token_version`` 重簽 access（管理員重設密碼 / 停用後 tv 已變），
  有效權限即時重算；只 flush。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import Clock
from app.core.errors import UnauthenticatedError
from app.core.permissions import resolve_effective_permissions
from app.core.request_meta import RequestMeta
from app.core.security.passwords import (
    dummy_verify,
    hash_password,
    needs_rehash,
    verify_password,
)
from app.core.security.tokens import create_access_token
from app.models.account import StaffUser
from app.services.auth import refresh_tokens
from app.services.auth.throttle import AuthThrottles, login_keys

INVALID_CREDENTIALS_MESSAGE: Final = "帳號或密碼錯誤"
_UNKNOWN_IP: Final = "-"


@dataclass(frozen=True)
class StaffSession:
    staff: StaffUser
    permissions: frozenset[str]
    access_token: str
    refresh_token: str


def _invalid_credentials() -> UnauthenticatedError:
    return UnauthenticatedError(INVALID_CREDENTIALS_MESSAGE, code="invalid_credentials")


def _revoke_family_and_commit(session: Session, raw: str, clock: Clock) -> None:
    """撤銷 raw 所屬 family 並 commit：呼叫端接著 raise，endpoint 的 rollback 不會退回撤銷。"""
    refresh_tokens.revoke_family_by_raw(session, raw, clock=clock)
    session.commit()


def _staff_session(staff: StaffUser, *, refresh_token: str, clock: Clock) -> StaffSession:
    access_token = create_access_token(
        subject_type="staff", subject_id=staff.id, token_version=staff.token_version, clock=clock
    )
    permissions = resolve_effective_permissions(
        staff.role.permissions, staff.extra_permissions, staff.revoked_permissions
    )
    return StaffSession(
        staff=staff,
        permissions=permissions,
        access_token=access_token,
        refresh_token=refresh_token,
    )


def login(
    session: Session,
    *,
    username: str,
    password: str,
    meta: RequestMeta,
    throttles: AuthThrottles,
    clock: Clock,
) -> StaffSession:
    username = username.strip().lower()
    now = clock.now()

    throttles.login_ip.hit(meta.ip or _UNKNOWN_IP, now)
    account_ip_key, account_key = login_keys(username, meta.ip)
    throttles.login_account_ip.check(account_ip_key, now)
    throttles.login_account.check(account_key, now)

    def record_failure() -> None:
        throttles.login_account_ip.record_failure(account_ip_key, now)
        throttles.login_account.record_failure(account_key, now)

    staff = session.execute(
        select(StaffUser).where(StaffUser.username == username)
    ).scalar_one_or_none()
    if staff is None:
        dummy_verify(password)
        record_failure()
        raise _invalid_credentials()

    # 停用帳號仍先驗密碼，維持與密碼錯誤相同的回應時間
    password_ok = verify_password(password, staff.password_hash)
    if not password_ok or not staff.is_active:
        record_failure()
        raise _invalid_credentials()

    throttles.login_account_ip.clear(account_ip_key)
    throttles.login_account.clear(account_key)

    if needs_rehash(staff.password_hash):
        staff.password_hash = hash_password(password)
    staff.last_login_at = now

    issued = refresh_tokens.issue(session, subject_type="staff", subject_id=staff.id, clock=clock)
    result = _staff_session(staff, refresh_token=issued.raw, clock=clock)
    session.flush()
    return result


def refresh(session: Session, *, raw_refresh: str | None, clock: Clock) -> StaffSession:
    if not raw_refresh:
        raise UnauthenticatedError
    rotated = refresh_tokens.rotate(session, raw_refresh, clock=clock)
    if rotated.subject_type != "staff":
        _revoke_family_and_commit(session, rotated.raw, clock)
        raise UnauthenticatedError(code="refresh_invalid")
    staff = session.execute(
        select(StaffUser).where(StaffUser.id == rotated.subject_id)
    ).scalar_one_or_none()
    if staff is None or not staff.is_active:
        _revoke_family_and_commit(session, rotated.raw, clock)
        raise UnauthenticatedError
    result = _staff_session(staff, refresh_token=rotated.raw, clock=clock)
    session.flush()
    return result
