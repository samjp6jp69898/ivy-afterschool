"""BACKEND-011：app/core/request_meta.py（RequestMeta：client IP、User-Agent、request id）。"""

from collections.abc import MutableMapping
from typing import Any

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.websockets import WebSocket

from app.core.logging import RequestContextMiddleware
from app.core.request_meta import RequestMeta, get_request_meta, get_ws_meta


def _request(
    client: tuple[str, int] | None = ("203.0.113.5", 1234), headers: dict[str, str] | None = None
) -> Request:
    raw_headers = [(k.lower().encode(), v.encode("latin-1")) for k, v in (headers or {}).items()]
    scope: dict[str, Any] = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "query_string": b"",
        "headers": raw_headers,
        "client": client,
        "state": {},
    }
    return Request(scope)


def test_request_meta_ip_valid() -> None:
    meta = get_request_meta(_request(headers={"User-Agent": "Mozilla/5.0"}))

    assert meta == RequestMeta(ip="203.0.113.5", user_agent="Mozilla/5.0", request_id=None)
    assert get_request_meta(_request(client=("2001:db8::1", 1))).ip == "2001:db8::1"


def test_request_meta_ip_invalid_to_none() -> None:
    assert get_request_meta(_request(client=("testclient", 50000))).ip is None
    assert get_request_meta(_request(client=None)).ip is None
    assert get_request_meta(_request(client=("", 1))).ip is None


def test_request_meta_ignores_forwarded_headers() -> None:
    """代理標頭由 uvicorn --proxy-headers 處理，本函式不自行解析（無法被用戶端偽造）。"""
    meta = get_request_meta(
        _request(headers={"X-Forwarded-For": "198.51.100.9", "X-Real-IP": "198.51.100.9"})
    )

    assert meta.ip == "203.0.113.5"


def test_request_meta_ua_truncated() -> None:
    meta = get_request_meta(_request(headers={"User-Agent": "a" * 800}))

    assert meta.user_agent is not None
    assert len(meta.user_agent) == 500
    assert get_request_meta(_request()).user_agent is None


def test_request_meta_request_id() -> None:
    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)

    @app.get("/meta")
    def _meta(meta: RequestMeta = Depends(get_request_meta)) -> dict[str, str | None]:  # noqa: B008
        return {"request_id": meta.request_id, "ip": meta.ip, "user_agent": meta.user_agent}

    response = TestClient(app).get("/meta", headers={"User-Agent": "probe-agent/1.0"})

    assert response.status_code == 200
    body = response.json()
    assert body["request_id"] == response.headers["X-Request-ID"]
    assert body["user_agent"] == "probe-agent/1.0"
    # TestClient 的 client host 是 'testclient'，不是合法 IP
    assert body["ip"] is None


def test_request_meta_ws() -> None:
    scope: dict[str, Any] = {
        "type": "websocket",
        "path": "/ws",
        "query_string": b"",
        "headers": [(b"user-agent", b"ws-agent/1.0")],
        "client": ("203.0.113.7", 4321),
        "state": {},
    }

    async def _receive() -> MutableMapping[str, Any]:
        return {"type": "websocket.connect"}

    async def _send(message: MutableMapping[str, Any]) -> None:
        return None

    meta = get_ws_meta(WebSocket(scope, _receive, _send))

    assert meta == RequestMeta(ip="203.0.113.7", user_agent="ws-agent/1.0", request_id=None)
