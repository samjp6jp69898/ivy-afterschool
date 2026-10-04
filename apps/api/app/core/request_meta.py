"""BACKEND-011：RequestMeta（client IP、User-Agent、request id）dependency。

供稽核（BACKEND-102）與登入節流（BACKEND-040）取用。

- IP 取 ``request.client.host``。api 只在 Railway private network 後面，由 web 的 nginx 反代：
  nginx 以 real_ip 模組取得真實來源 IP，並以 ``X-Forwarded-For: $remote_addr`` 覆寫（不 append
  用戶端自帶的值）；uvicorn 以 ``--proxy-headers`` 啟動且 ``--forwarded-allow-ips`` 只信任該 nginx
  （INFRA-031 / INFRA-033）。代理標頭由 uvicorn 處理，本模組**不**自行解析 X-Forwarded-For。
- IP 字串需能被 ``ipaddress.ip_address`` 解析，否則存 None（audit_logs.ip 為 inet）。
- user_agent 截斷 500 字；request_id 取 BACKEND-004 的 ``request_id_var``。

移植 ivy ``utils/request_ip.py::get_client_ip``；去掉 TRUSTED_PROXY_IPS 與 XFF 鏈解析。
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from typing import Final

from starlette.requests import HTTPConnection, Request
from starlette.websockets import WebSocket

from app.core.logging import request_id_var

USER_AGENT_MAX_LEN: Final = 500


@dataclass(frozen=True)
class RequestMeta:
    ip: str | None
    user_agent: str | None
    request_id: str | None


def _client_ip(conn: HTTPConnection) -> str | None:
    host = conn.client.host if conn.client else None
    if not host:
        return None
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        return None


def _user_agent(conn: HTTPConnection) -> str | None:
    value = conn.headers.get("user-agent")
    if not value:
        return None
    return value[:USER_AGENT_MAX_LEN]


def _meta(conn: HTTPConnection) -> RequestMeta:
    return RequestMeta(
        ip=_client_ip(conn), user_agent=_user_agent(conn), request_id=request_id_var.get()
    )


def get_request_meta(request: Request) -> RequestMeta:
    """FastAPI dependency。"""
    return _meta(request)


def get_ws_meta(websocket: WebSocket) -> RequestMeta:
    return _meta(websocket)
