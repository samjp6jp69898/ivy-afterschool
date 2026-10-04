"""BACKEND-004：logging 設定、request id middleware、敏感欄位遮罩。

- ``configure_logging(settings)``：root logger level INFO、單一 stderr handler。production 輸出單行
  JSON（ts、level、logger、msg、request_id），其他環境人類可讀格式；handler 掛 ``RedactingFilter``。
  uvicorn 的 access logger 會印完整 URL（含 query string），一律停用，改由本模組的 access log 取代。
- ``RequestContextMiddleware``：純 ASGI middleware（http 與 websocket）。沿用合法的入站
  ``X-Request-ID``（``^[A-Za-z0-9-]{8,64}$``），否則重新產生 uuid4 hex；寫進 ``request_id_var`` 與
  ``scope["state"]["request_id"]``（即 ``request.state.request_id``，BACKEND-003 的錯誤 handler
  讀此值）。
  http 回應加 ``X-Request-ID``，請求結束記一行 access log（method、path、status、duration_ms；
  不含 query string，``/api/health`` 不記）。websocket 只設定 request id，不記 access log。
- ``RedactingFilter``：把 msg 與 args 中敏感 key（password、token、code、cookie、authorization、
  secret 等）的值遮成 ``***``；dict 遞迴處理，字串另以 ``key=value`` / ``'key': 'value'`` 樣式比對。
"""

from __future__ import annotations

import json
import logging
import re
import sys
import time
import uuid
from collections.abc import Mapping
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import Settings

ACCESS_LOGGER_NAME = "app.access"
REQUEST_ID_HEADER = "X-Request-ID"
REDACTED = "***"
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9-]{8,64}$")
_NO_ACCESS_LOG_PATHS = frozenset({"/api/health"})
# 精確比對的 key（避免 status_code / error_code 被誤遮）與子字串比對的 key
_SENSITIVE_EXACT = frozenset({"code"})
_SENSITIVE_SUBSTRINGS = (
    "password",
    "passwd",
    "token",
    "secret",
    "cookie",
    "authorization",
    "binding_code",
)
# 字串層遮罩：key=value（value 取到下一個空白 / 逗號）與 'key': 'value'（引號單雙皆可、值可為裸字）
_KEY_VALUE_RE = re.compile(r"(?P<key>[A-Za-z_][A-Za-z0-9_-]*)=(?P<value>[^\s,，;]+)")
_QUOTED_KEY_VALUE_RE = re.compile(
    r"""(?P<kq>['"])(?P<key>[A-Za-z_][A-Za-z0-9_-]*)(?P=kq)\s*:\s*"""
    r"""(?P<value>'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*"|[^\s,，}\]{'"]+)"""
)

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
access_logger = logging.getLogger(ACCESS_LOGGER_NAME)


# --- 遮罩 ---------------------------------------------------------------------------------


def is_sensitive_key(key: object) -> bool:
    name = str(key).lower()
    return name in _SENSITIVE_EXACT or any(s in name for s in _SENSITIVE_SUBSTRINGS)


def redact_mapping(value: Any) -> Any:
    """遞迴複製 dict / list，敏感 key 的值換成 ***；其他型別原樣回傳。"""
    if isinstance(value, Mapping):
        return {k: REDACTED if is_sensitive_key(k) else redact_mapping(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return type(value)(redact_mapping(item) for item in value)
    return value


def _replace_kv(match: re.Match[str]) -> str:
    if is_sensitive_key(match.group("key")):
        return f"{match.group('key')}={REDACTED}"
    return match.group(0)


def _replace_quoted(match: re.Match[str]) -> str:
    if is_sensitive_key(match.group("key")):
        kq = match.group("kq")
        return f"{kq}{match.group('key')}{kq}: '{REDACTED}'"
    return match.group(0)


def redact_text(text: str) -> str:
    text = _KEY_VALUE_RE.sub(_replace_kv, text)
    return _QUOTED_KEY_VALUE_RE.sub(_replace_quoted, text)


class RedactingFilter(logging.Filter):
    """先遮 args 內的 dict，再 format 成字串對 key=value 樣式遮罩，最後清掉 args。

    任何例外都不擋 record（回傳 True），只退回對原始 msg 做字串遮罩。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if not isinstance(record.msg, str):
                return True
            if record.args:
                record.args = redact_mapping(record.args)
                record.msg = redact_text(record.getMessage())
                record.args = None
            else:
                record.msg = redact_text(record.msg)
        except Exception:
            if isinstance(record.msg, str):
                record.msg = redact_text(record.msg)
        return True


# --- formatter ----------------------------------------------------------------------------


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": getattr(record, "request_id", None),
        }
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False, default=str)


class _TextFormatter(logging.Formatter):
    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        if getattr(record, "request_id", None) is None:
            record.request_id = "-"
        return super().format(record)


class _AppStreamHandler(logging.StreamHandler):  # type: ignore[type-arg]
    """標記為本模組安裝的 handler，configure_logging 重複呼叫時只換掉自己的。"""


def configure_logging(settings: Settings) -> None:
    root = logging.getLogger()
    for handler in list(root.handlers):
        if isinstance(handler, _AppStreamHandler):
            root.removeHandler(handler)
    handler = _AppStreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter() if settings.is_production else _TextFormatter())
    handler.addFilter(RedactingFilter())
    handler.addFilter(_RequestIdFilter())
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    # uvicorn 的 access log 含完整 URL（query string 可能帶綁定碼 / token），由本模組的 access log
    # 取代；uvicorn / uvicorn.error 交給 root handler 統一格式
    logging.getLogger("uvicorn.access").disabled = True
    for name in ("uvicorn", "uvicorn.error"):
        uv_logger = logging.getLogger(name)
        uv_logger.handlers.clear()
        uv_logger.propagate = True


# --- middleware ---------------------------------------------------------------------------


def _incoming_request_id(scope: Scope) -> str:
    for name, value in scope.get("headers") or ():
        if name == b"x-request-id":
            candidate = str(value.decode("latin-1"))
            if _REQUEST_ID_RE.match(candidate):
                return candidate
            break
    return uuid.uuid4().hex


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        request_id = _incoming_request_id(scope)
        scope.setdefault("state", {})["request_id"] = request_id
        token = request_id_var.set(request_id)
        try:
            if scope["type"] == "websocket":
                await self.app(scope, receive, send)
                return
            await self._handle_http(scope, receive, send, request_id)
        finally:
            request_id_var.reset(token)

    async def _handle_http(
        self, scope: Scope, receive: Receive, send: Send, request_id: str
    ) -> None:
        status = 500  # app 未送出回應就拋例外時視為 500
        started = time.perf_counter()

        async def send_with_request_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = list(message.get("headers") or [])
                headers.append((REQUEST_ID_HEADER.lower().encode(), request_id.encode()))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            path = scope.get("path", "")
            if path not in _NO_ACCESS_LOG_PATHS:
                duration_ms = (time.perf_counter() - started) * 1000
                access_logger.info(
                    "%s %s %d %.1fms", scope.get("method", "-"), path, status, duration_ms
                )
