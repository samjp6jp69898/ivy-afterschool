"""BACKEND-051：以 LINE verify API 驗證 LIFF id_token，aud 嚴格比對、重放防護。

移植 ivy ``services/line_login_service.py::LineLoginService.verify_id_token`` 與
``_check_id_token_replay``；去掉 tenant / env fallback。channel_id 由呼叫端從
system_settings ``line.liff.channel_id`` 讀取後傳入。

- 連不到 LINE（連線失敗、逾時、5xx、429）→ 503 ``line_unavailable``；token 本身有問題 → 401。
- 重放：全部檢查通過後才以 sha256(id_token) 記錄（TTL 5 分鐘），檢查與記錄在同一把鎖內。
- log 只記 sha256 前 8 碼，不記 id_token 原文；LINE 的錯誤內容不回給用戶端。
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final, Protocol, runtime_checkable

import httpx

from app.core.clock import Clock, get_clock
from app.core.errors import AppError

logger = logging.getLogger(__name__)

LINE_VERIFY_URL: Final = "https://api.line.me/oauth2/v2.1/verify"
_TIMEOUT_SECONDS: Final = 5.0
_REPLAY_TTL: Final = timedelta(minutes=5)
_DISPLAY_NAME_MAX: Final = 100
_LINE_USER_ID = re.compile(r"U[0-9a-f]{32}")


@dataclass(frozen=True)
class LineProfile:
    line_user_id: str
    display_name: str | None
    picture_url: str | None


@runtime_checkable
class LineIdTokenVerifier(Protocol):
    def verify(self, id_token: str, *, channel_id: str) -> LineProfile: ...


def _unavailable(message: str = "LINE 驗證服務暫時無法連線") -> AppError:
    return AppError("line_unavailable", message, status=503)


def _invalid() -> AppError:
    return AppError("invalid_id_token", "LINE 登入驗證失敗，請重新開啟", status=401)


def _digest(id_token: str) -> str:
    return hashlib.sha256(id_token.encode("utf-8")).hexdigest()


def _optional_str(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


class HttpLineIdTokenVerifier:
    def __init__(self, *, transport: httpx.BaseTransport | None = None, clock: Clock) -> None:
        self._client = httpx.Client(transport=transport, timeout=_TIMEOUT_SECONDS)
        self._clock = clock
        self._seen: dict[str, datetime] = {}  # sha256(id_token) → 到期時間
        self._lock = threading.Lock()

    def verify(self, id_token: str, *, channel_id: str) -> LineProfile:
        if not channel_id:
            raise _unavailable("LINE 登入尚未設定")
        if not id_token:
            raise _invalid()
        digest = _digest(id_token)

        payload = self._call_line(id_token, channel_id, digest)

        aud = payload.get("aud")
        if not isinstance(aud, str) or not aud or aud != channel_id:
            logger.warning("LINE id_token aud 不符 expected=%s got=%r", channel_id, aud)
            raise _invalid()
        sub = payload.get("sub")
        if not isinstance(sub, str) or not _LINE_USER_ID.fullmatch(sub):
            logger.warning("LINE id_token sub 格式不符 token=%s...", digest[:8])
            raise _invalid()

        self._record_or_reject(digest)

        name = _optional_str(payload.get("name"))
        return LineProfile(
            line_user_id=sub,
            display_name=name[:_DISPLAY_NAME_MAX] if name is not None else None,
            picture_url=_optional_str(payload.get("picture")),
        )

    def _call_line(self, id_token: str, channel_id: str, digest: str) -> dict[str, Any]:
        try:
            response = self._client.post(
                LINE_VERIFY_URL, data={"id_token": id_token, "client_id": channel_id}
            )
        except httpx.HTTPError as exc:
            # 例外訊息可能帶請求內容，只記型別
            logger.warning("LINE verify 連線失敗：%s", type(exc).__name__)
            raise _unavailable() from None

        status = response.status_code
        if status >= 500 or status == 429:
            logger.warning("LINE verify 服務異常 status=%s", status)
            raise _unavailable()
        if status != 200:
            logger.warning("LINE verify 拒絕 status=%s token=%s...", status, digest[:8])
            raise _invalid()
        try:
            payload = response.json()
        except ValueError:
            logger.warning("LINE verify 回應不是 JSON token=%s...", digest[:8])
            raise _invalid() from None
        if not isinstance(payload, dict):
            logger.warning("LINE verify 回應不是物件 token=%s...", digest[:8])
            raise _invalid()
        return payload

    def _record_or_reject(self, digest: str) -> None:
        now = self._clock.now()
        with self._lock:
            for key in [k for k, expires in self._seen.items() if expires <= now]:
                del self._seen[key]
            if digest in self._seen:
                logger.warning("LINE id_token 重放 token=%s...", digest[:8])
                raise AppError(
                    "id_token_replayed", "此 LINE 登入已使用過，請重新從 LIFF 開啟", status=401
                )
            self._seen[digest] = now + _REPLAY_TTL


_VERIFIER: HttpLineIdTokenVerifier | None = None
_VERIFIER_LOCK = threading.Lock()


def get_line_verifier() -> LineIdTokenVerifier:
    """FastAPI dependency：module 單例（重放 cache 必須跨 request 共用）。"""
    global _VERIFIER
    with _VERIFIER_LOCK:
        if _VERIFIER is None:
            _VERIFIER = HttpLineIdTokenVerifier(clock=get_clock())
        return _VERIFIER
