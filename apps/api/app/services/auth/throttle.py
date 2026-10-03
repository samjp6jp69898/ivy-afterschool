"""BACKEND-040：認證相關節流（登入、改密碼、LIFF、綁定）。

以 BACKEND-012 的元件組出，移植 ivy ``api/auth.py`` 的 IP 視窗 / 連續失敗鎖定參數與
``api/parent_portal/auth.py`` 的 bind lockout。

- IP 維度（login_ip / liff_ip）依賴 BACKEND-011 取得、不可由用戶端偽造的 client IP；
  帳號維度（login_account / login_account_ip / bind）另外把關，與 IP 無關。
- 每個欄位都以 default_factory 建立，``AuthThrottles()`` 之間不共用狀態；
  測試以 dependency override 換成新實例。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

from app.core.rate_limit import FailureLockout, SlidingWindowLimiter

_UNKNOWN_IP: Final = "-"


@dataclass
class AuthThrottles:
    # 同 IP 5 分鐘 20 次登入嘗試
    login_ip: SlidingWindowLimiter = field(
        default_factory=lambda: SlidingWindowLimiter(max_hits=20, window_seconds=300)
    )
    # (username, ip) 連續失敗 5 次鎖 15 分
    login_account_ip: FailureLockout = field(
        default_factory=lambda: FailureLockout(threshold=5, lockout_seconds=900)
    )
    # 同帳號跨來源失敗 20 次鎖 15 分（分散式猜測）
    login_account: FailureLockout = field(
        default_factory=lambda: FailureLockout(threshold=20, lockout_seconds=900)
    )
    # 改密碼時舊密碼錯 5 次
    password_change: FailureLockout = field(
        default_factory=lambda: FailureLockout(threshold=5, lockout_seconds=900)
    )
    liff_ip: SlidingWindowLimiter = field(
        default_factory=lambda: SlidingWindowLimiter(max_hits=30, window_seconds=300)
    )
    # key：line_user_id 或 parent id
    bind: FailureLockout = field(
        default_factory=lambda: FailureLockout(threshold=5, lockout_seconds=900)
    )


_THROTTLES: Final = AuthThrottles()


def get_auth_throttles() -> AuthThrottles:
    """FastAPI dependency：回傳 module 單例（api 單一實例單 worker，狀態放記憶體）。"""
    return _THROTTLES


def login_keys(username: str, ip: str | None) -> tuple[str, str]:
    """回傳 (login_account_ip 的 key, login_account 的 key)。

    username 先 strip().lower()（縱深防禦：呼叫端漏正規化也不會以大小寫變化繞過鎖定）。
    ip 由伺服器取得、不含 ``|``，以最後一個 ``|`` 切分即可還原，key 為單射。
    """
    account = username.strip().lower()
    return f"{account}|{ip or _UNKNOWN_IP}", account
