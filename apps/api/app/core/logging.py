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
  secret 等）的值遮成 ``***``；dict / list / tuple 遞迴處理（含 namedtuple 與 header pair
  ``("authorization", "Bearer x")`` / ``(b"cookie", b"...")``），字串另以 ``key=value``、
  ``key: value``（整個值到行尾或分隔符）與 ``'key': 'value'`` 樣式比對；遮罩失敗時 fail-closed。
  非字串 msg（BACKEND-539）：Mapping / list / tuple 先 ``redact_mapping`` 再轉字串；其他物件
  ``str()`` 後以 ``redact_text`` 遮罩。
- 例外 traceback 與 ``stack_info``（BACKEND-539）：兩種 formatter 寫出前都先經 ``redact_text``
  （不使用 ``record.exc_text`` 快取，避免拿到其他 handler 存下的未遮罩文字）；檔名、行號與例外型別
  名稱保留。
- ``scrub_sentry_event`` / ``scrub_sentry_breadcrumb``（BACKEND-020）：Sentry 的 LoggingIntegration
  在 ``Logger.callHandlers`` 階段就讀 record，會繞過 handler 級的 RedactingFilter，
  ``capture_exception`` 也會原樣送出例外訊息；所以 ``sentry_sdk.init`` 掛 ``before_send`` /
  ``before_breadcrumb``，整個 event / breadcrumb 深度走訪：敏感 key 的值換成 ***、所有字串經
  ``redact_text``，``request.cookies`` 整個遮掉；遮罩過程拋例外時回傳 None（丟棄，fail-closed）。
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
from typing import Any, cast

from sentry_sdk.types import Event, Hint
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
# 字串層遮罩只針對敏感 key（非敏感 key 的值可能含空白，不能用通用 key=value 掃描）：
# key=value 與 header 樣式 key: value，值取到行尾 / 分隔符（, ; 全形逗號）或下一個 key= / key:
# 之前，因此 'authorization=Bearer T5' 整段遮掉、'token=x status_code=200' 不吞掉後面的 pair。
# & 不是終止符：'password=Abc&123' 要整段遮掉，query string 的後續參數一起遮屬 fail-safe。
# lookbehind 只排除識別字字元（status_code 的 code 不算），引號內的字面值（traceback 原始碼行的
# "password=x"）照樣遮。
_SENSITIVE_KEY_PATTERN = (
    r"[A-Za-z0-9_-]*(?:" + "|".join(_SENSITIVE_SUBSTRINGS) + r")[A-Za-z0-9_-]*|code"
)
_KEY_VALUE_RE = re.compile(
    r"(?<![A-Za-z0-9_-])(?P<key>" + _SENSITIVE_KEY_PATTERN + r")\s*[=:]\s*"
    r"(?P<value>(?:(?!\s+[A-Za-z_][A-Za-z0-9_-]*\s*[=:])[^,，;\n])+)",
    re.IGNORECASE,
)
# 'key': 'value' / "key": "value"（dict repr），值為帶引號字串或裸 token
_QUOTED_KEY_VALUE_RE = re.compile(
    r"""(?P<kq>['"])(?P<key>[A-Za-z_][A-Za-z0-9_-]*)(?P=kq)\s*:\s*"""
    r"""(?P<value>'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*"|[^\s,，}\]{'"]+)"""
)

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
access_logger = logging.getLogger(ACCESS_LOGGER_NAME)


# --- 遮罩 ---------------------------------------------------------------------------------


def is_sensitive_key(key: object) -> bool:
    if isinstance(key, bytes | bytearray):
        key = bytes(key).decode("latin-1")
    if not isinstance(key, str):
        return False
    name = key.lower()
    return name in _SENSITIVE_EXACT or any(s in name for s in _SENSITIVE_SUBSTRINGS)


def _redacted_like(value: object) -> object:
    return REDACTED.encode() if isinstance(value, bytes | bytearray) else REDACTED


def _rebuild_sequence(value: tuple[Any, ...] | list[Any], items: list[Any]) -> Any:
    if type(value) in (tuple, list):
        return type(value)(items)
    if isinstance(value, tuple) and hasattr(value, "_fields"):  # namedtuple
        return type(value)(*items)
    try:
        return type(value)(items)
    except Exception:
        return tuple(items)


def redact_mapping(value: Any) -> Any:
    """遞迴複製 dict / list / tuple，敏感 key 的值換成 ***；其他型別原樣回傳。

    二元 tuple / list 的第一個元素是敏感 key（str 或 bytes）時視為 header pair，第二個元素遮掉
    （HTTP header 清單、ASGI ``scope["headers"]``）。
    """
    if isinstance(value, Mapping):
        return {k: REDACTED if is_sensitive_key(k) else redact_mapping(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        if len(value) == 2 and is_sensitive_key(value[0]):
            items = [value[0], _redacted_like(value[1])]
        else:
            items = [redact_mapping(item) for item in value]
        return _rebuild_sequence(value, items)
    return value


def _replace_kv(match: re.Match[str]) -> str:
    return f"{match.group('key')}={REDACTED}"


def _replace_quoted(match: re.Match[str]) -> str:
    if is_sensitive_key(match.group("key")):
        kq = match.group("kq")
        return f"{kq}{match.group('key')}{kq}: '{REDACTED}'"
    return match.group(0)


def redact_text(text: str) -> str:
    text = _KEY_VALUE_RE.sub(_replace_kv, text)
    return _QUOTED_KEY_VALUE_RE.sub(_replace_quoted, text)


def _coerce_msg(msg: object) -> str:
    """非字串 msg 轉成已遮罩的字串：容器先結構化遮罩，其他物件 str() 後做文字遮罩。"""
    if isinstance(msg, Mapping | list | tuple):
        msg = redact_mapping(msg)
    return redact_text(str(msg))


class RedactingFilter(logging.Filter):
    """先遮 args 內的 dict / pair，再 format 成字串對 key=value 樣式遮罩，最後清掉 args。

    任何例外都不擋 record（回傳 True），但退路 fail-closed：args 整個換成 ***、msg 只保留遮罩後的
    樣板文字（msg 不是字串時整個換成 ***），不保留未遮罩的原始內容。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        original_msg = record.msg
        try:
            if not isinstance(record.msg, str):
                record.msg = _coerce_msg(record.msg)
            if record.args:
                record.args = redact_mapping(record.args)
                record.msg = redact_text(record.getMessage())
                record.args = None
            else:
                record.msg = redact_text(record.msg)
        except Exception:
            template = redact_text(original_msg) if isinstance(original_msg, str) else REDACTED
            record.msg = f"{template} args={REDACTED}"
            record.args = None
        return True


# --- Sentry（BACKEND-020）-----------------------------------------------------------------


def _scrub_deep(value: Any) -> Any:
    """深度複製並遮罩：敏感 key 的值換成 ***、字串經 redact_text、容器遞迴。"""
    if isinstance(value, Mapping):
        return {k: REDACTED if is_sensitive_key(k) else _scrub_deep(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return _rebuild_sequence(value, [_scrub_deep(item) for item in value])
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, bytes | bytearray):
        return redact_text(bytes(value).decode("latin-1"))
    return value


def scrub_sentry_event(event: Event, hint: Hint) -> Event | None:
    """``sentry_sdk.init(before_send=...)``：遮罩失敗回 None（丟棄整個 event）。"""
    try:
        scrubbed: dict[str, Any] = _scrub_deep(event)
        request = scrubbed.get("request")
        if isinstance(request, dict) and "cookies" in request:
            request["cookies"] = REDACTED
        return cast(Event, scrubbed)  # TypedDict 深度複製後仍是同形狀 dict
    except Exception:
        return None


def scrub_sentry_breadcrumb(crumb: dict[str, Any], hint: Hint) -> dict[str, Any] | None:
    """``sentry_sdk.init(before_breadcrumb=...)``：遮罩失敗回 None（丟棄該 breadcrumb）。"""
    try:
        result: dict[str, Any] = _scrub_deep(crumb)
        return result
    except Exception:
        return None


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
            entry["exc"] = redact_text(self.formatException(record.exc_info))
        if record.stack_info:
            entry["stack"] = redact_text(self.formatStack(record.stack_info))
        return json.dumps(entry, ensure_ascii=False, default=str)


class _TextFormatter(logging.Formatter):
    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        if getattr(record, "request_id", None) is None:
            record.request_id = "-"
        # 不走 logging.Formatter.format：它會沿用 record.exc_text 快取（可能是別的 handler 存下的
        # 未遮罩文字），traceback 與 stack 一律現算並遮罩
        record.message = record.getMessage()
        record.asctime = self.formatTime(record, self.datefmt)
        output = self.formatMessage(record)
        if record.exc_info:
            output = output.rstrip("\n") + "\n" + redact_text(self.formatException(record.exc_info))
        if record.stack_info:
            output = output.rstrip("\n") + "\n" + redact_text(self.formatStack(record.stack_info))
        return output


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
            if _REQUEST_ID_RE.fullmatch(candidate):
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
