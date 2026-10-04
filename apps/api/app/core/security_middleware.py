"""BACKEND-019：Origin 檢查（CSRF 縱深防禦）與安全標頭。

cookie 認證 + SameSite=Lax 是主要 CSRF 防線，本 middleware 為第二道。純 ASGI middleware，同時處理
http 與 websocket scope。

- 允許來源 = ``Settings.cors_origins`` 加上 ``public_base_url`` 的 origin（``allowed_origins()``；
  比對前都正規化：scheme / host 小寫、去掉預設 port）。
- HTTP 不安全方法（POST / PUT / PATCH / DELETE）：有 ``Origin`` 且不在允許集合 → 403
  ``origin_forbidden``（domain_spec §2 錯誤格式）；沒有 Origin 但有 ``Referer`` → 取其 origin 比對；
  兩者皆無 → 放行（非瀏覽器客戶端、伺服器內部呼叫）。GET / HEAD / OPTIONS 不檢查。
- WebSocket handshake：``Origin`` 存在且不在允許集合 → accept 前以 close code 4403 拒絕；沒有 Origin
  （非瀏覽器）放行。
- 所有 http 回應加 ``X-Content-Type-Options: nosniff``、``Referrer-Policy: same-origin``、
  ``X-Frame-Options: DENY``；``/api/`` 路徑另加 ``Cache-Control: no-store``（避免瀏覽器快取個資）。
  標頭清單以 ``security_headers_for(path)`` 公開，BACKEND-003 的 500 handler 共用（未處理例外的 500
  由最外層的 ServerErrorMiddleware 產生，不經過本 middleware；BACKEND-020）。
"""

from __future__ import annotations

import logging
from urllib.parse import urlsplit

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import Settings

logger = logging.getLogger(__name__)

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
WS_CLOSE_ORIGIN_FORBIDDEN = 4403
ORIGIN_FORBIDDEN_CODE = "origin_forbidden"
ORIGIN_FORBIDDEN_MESSAGE = "來源不被允許"
_DEFAULT_PORTS = {"http": 80, "https": 443}
API_PREFIX = "/api/"
# 公開給 BACKEND-003 的 500 handler 共用（未處理例外的 500 不經過本 middleware）
SECURITY_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"same-origin"),
    (b"x-frame-options", b"DENY"),
)
NO_STORE_HEADER = (b"cache-control", b"no-store")


def security_headers_for(path: str) -> list[tuple[bytes, bytes]]:
    """該 path 的安全標頭；``/api/`` 路徑另加 ``Cache-Control: no-store``。"""
    headers = list(SECURITY_HEADERS)
    if path.startswith(API_PREFIX):
        headers.append(NO_STORE_HEADER)
    return headers


def normalize_origin(value: str) -> str | None:
    """把 origin 或 URL 正規化成 ``scheme://host[:port]``；不是合法 http(s) URL 時回 None。"""
    try:
        parts = urlsplit(value.strip())
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme not in _DEFAULT_PORTS or not parts.hostname:
        return None
    try:
        port = parts.port
    except ValueError:
        return None
    host = parts.hostname.lower()
    if port is None or port == _DEFAULT_PORTS[scheme]:
        return f"{scheme}://{host}"
    return f"{scheme}://{host}:{port}"


def allowed_origins(settings: Settings) -> frozenset[str]:
    candidates = [*settings.cors_origins, settings.public_base_url_str]
    return frozenset(o for raw in candidates if (o := normalize_origin(raw)) is not None)


def _header(scope: Scope, name: bytes) -> str | None:
    for key, value in scope.get("headers") or ():
        if key == name:
            return str(value.decode("latin-1"))
    return None


class SecurityMiddleware:
    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.allowed = allowed_origins(settings)

    def _is_allowed(self, origin: str) -> bool:
        normalized = normalize_origin(origin)
        return normalized is not None and normalized in self.allowed

    def _http_origin_ok(self, scope: Scope) -> bool:
        origin = _header(scope, b"origin")
        if origin is not None:
            return self._is_allowed(origin)
        referer = _header(scope, b"referer")
        if referer is not None:
            return self._is_allowed(referer)
        return True

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "websocket":
            origin = _header(scope, b"origin")
            if origin is not None and not self._is_allowed(origin):
                logger.warning("WS Origin 不被允許 path=%s", scope.get("path"))
                await receive()  # 先收 websocket.connect，再在 accept 前 close
                await send({"type": "websocket.close", "code": WS_CLOSE_ORIGIN_FORBIDDEN})
                return
            await self.app(scope, receive, send)
            return
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = str(scope.get("path", ""))
        extra_headers = security_headers_for(path)

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = [
                    (k, v)
                    for k, v in (message.get("headers") or [])
                    if k not in {name for name, _ in extra_headers}
                ]
                headers.extend(extra_headers)
                message["headers"] = headers
            await send(message)

        if scope["method"] in UNSAFE_METHODS and not self._http_origin_ok(scope):
            logger.warning("Origin / Referer 不被允許 %s %s", scope["method"], path)
            response = JSONResponse(
                status_code=403,
                content={
                    "error": {
                        "code": ORIGIN_FORBIDDEN_CODE,
                        "message": ORIGIN_FORBIDDEN_MESSAGE,
                        "details": None,
                    }
                },
            )
            await response(scope, receive, send_with_headers)
            return
        await self.app(scope, receive, send_with_headers)
