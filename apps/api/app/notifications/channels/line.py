"""BACKEND-207：LINE Messaging API push 用戶端（token 從 system_settings 解密、錯誤分類、重試鍵）。

移植 ivy ``services/notification/_channels/line.py::LineAdapter`` 與 ``line_service._push_to_user``
的 push 與錯誤處理；去掉群組推播、各事件 handler、tenant 設定解析、circuit breaker。

- ``X-Line-Retry-Key`` 以 outbox id 當重試鍵，LINE 端去重，重送不會重複推播。
- 結果分類：200 → ok；409（同一 retry key 已被接受）→ ok；429、5xx、timeout、連線錯誤 → retryable；
  其他 4xx（400 / 401 / 403 / 404，例如使用者封鎖官方帳號）→ 不可重試。
- ``error`` 截 2000 字，絕不含 token（例外訊息或回應內容若夾帶 token 一律遮罩）。

BACKEND-543：``close()``（冪等，關閉內部或注入的 ``httpx.Client``）與 context manager；關閉後再
push → ``RuntimeError``（訊息不含 token）。BACKEND-209 的 outbox_jobs 在 token 變更時對舊實例呼叫
``close()`` 釋放連線池。``http_client`` 供測試注入自備的 ``httpx.Client``，與 ``transport`` 互斥；
Authorization 一律由本類別逐請求補上，兩種建法行為相同。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Final, Protocol, Self
from uuid import UUID

import httpx
from sqlalchemy.orm import Session

from app.core.settings_registry import LINE_MESSAGING
from app.services.settings_service import get_setting

logger = logging.getLogger(__name__)

LINE_PUSH_URL: Final = "https://api.line.me/v2/bot/message/push"
ERROR_MAX_CHARS: Final = 2000
_BODY_SNIPPET_CHARS: Final = 500
_TOKEN_MASK: Final = "***"  # noqa: S105  遮罩字樣，不是密碼


@dataclass(frozen=True)
class LineSendResult:
    ok: bool
    retryable: bool
    error: str | None  # 截 2000 字，不含 token


class LineMessagingClient(Protocol):
    def push_text(self, *, to: str, text: str, retry_key: UUID) -> LineSendResult: ...


class HttpLineMessagingClient:
    def __init__(
        self,
        channel_access_token: str,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 5.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        if http_client is not None and transport is not None:
            raise ValueError("http_client 與 transport 只能擇一")
        self._token = channel_access_token
        self._client = http_client or httpx.Client(transport=transport, timeout=timeout)

    def close(self) -> None:
        """關閉連線池；重複呼叫無作用。"""
        self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def push_text(self, *, to: str, text: str, retry_key: UUID) -> LineSendResult:
        if self._client.is_closed:
            raise RuntimeError("LINE client 已關閉，不可再推播")
        payload = {"to": to, "messages": [{"type": "text", "text": text}]}
        headers = {
            "Authorization": f"Bearer {self._token}",
            "X-Line-Retry-Key": str(retry_key),
        }
        try:
            response = self._client.post(LINE_PUSH_URL, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            # 連線錯誤、timeout 等都可重試；例外訊息可能帶請求內容，記型別並遮罩 token
            error = self._sanitize(f"{type(exc).__name__}: {exc}")
            logger.warning("LINE push 連線失敗 retry_key=%s：%s", retry_key, type(exc).__name__)
            return LineSendResult(ok=False, retryable=True, error=error)

        status = response.status_code
        if status == 200 or status == 409:
            return LineSendResult(ok=True, retryable=False, error=None)
        error = self._sanitize(f"HTTP {status}: {response.text[:_BODY_SNIPPET_CHARS]}")
        retryable = status == 429 or status >= 500
        logger.warning(
            "LINE push 失敗 status=%s retryable=%s retry_key=%s", status, retryable, retry_key
        )
        return LineSendResult(ok=False, retryable=retryable, error=error)

    def _sanitize(self, message: str) -> str:
        return message.replace(self._token, _TOKEN_MASK)[:ERROR_MAX_CHARS]


def build_line_client(session: Session) -> LineMessagingClient | None:
    """從 system_settings 取 channel access token；未設定 → None（通道未啟用）。"""
    token = get_setting(session, LINE_MESSAGING).channel_access_token
    if not token:
        return None
    return HttpLineMessagingClient(token)
