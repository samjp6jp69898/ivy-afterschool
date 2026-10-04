"""BACKEND-225：WebSocket 共用（cookie 認證、關閉碼、定期重驗與訊息迴圈）。

移植 ivy ``utils/ws_hub.py::run_ws_connection`` / ``get_token_from_ws`` 與
``api/inbox_ws.py::_token_still_valid`` 的「握手驗證 + 連線中定期重驗」；去掉 query string token、
tenant、連線數限制、server 端 ping。

- ``ws_load_staff`` / ``ws_load_parent``：讀 ``staff_access`` / ``parent_access`` cookie，以
  ``open_session()`` 開短 session 交給 BACKEND-047 / 062 的 ``load_current_*``（``path=None``：
  強制改密碼的員工 403）。失敗拋 AppError，endpoint 以 ``close_code_for`` 轉關閉碼：401 → 4401、
  403 → 4403（與 BACKEND-019 origin 拒絕共用）、其他 → 4400。兩者都是同步函式（含 DB 存取），
  endpoint 以 ``run_in_thread`` 在 worker thread 執行，不阻塞 event loop。
- ``run_ws_loop``：同時等待 client 訊息與重驗計時器。訊息必須是 JSON 物件且有 ``action`` 字串，
  否則回 ``{"type":"error","code":"bad_message"}``；連續 ``MAX_BAD_MESSAGES`` 次 → close 4400
  （合法訊息會重設計數）。``{"action":"ping"}`` → ``{"type":"pong"}``（不進 on_message）。每
  ``interval`` 秒呼叫 ``revalidate()``，回 False 或拋例外 → close 4401。client 斷線 → 正常結束。
"""

from __future__ import annotations

import contextlib
import functools
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Final

import anyio
import anyio.to_thread
from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session
from starlette.requests import HTTPConnection

from app.api.deps import CurrentParent, CurrentStaff, load_current_parent, load_current_staff
from app.core import db as core_db
from app.core.clock import Clock
from app.core.errors import AppError
from app.core.security.cookies import PARENT_ACCESS, STAFF_ACCESS, read_cookie
from app.core.security_middleware import WS_CLOSE_ORIGIN_FORBIDDEN

logger = logging.getLogger(__name__)

WS_CLOSE_BAD_MESSAGE: Final = 4400
WS_CLOSE_UNAUTHENTICATED: Final = 4401
WS_CLOSE_FORBIDDEN: Final = WS_CLOSE_ORIGIN_FORBIDDEN  # 4403
REVALIDATE_SECONDS: Final = 60
MAX_BAD_MESSAGES: Final = 5

_BAD_MESSAGE: Final[dict[str, str]] = {"type": "error", "code": "bad_message"}
_PONG: Final[dict[str, str]] = {"type": "pong"}


def open_session() -> Session:
    """握手 / 重驗用的短 session（用完即關）；測試以 monkeypatch 換成綁測試連線的 session。"""
    return core_db.SessionLocal(bind=core_db.get_engine())


def ws_load_staff(ws: HTTPConnection, *, clock: Clock) -> CurrentStaff:
    with open_session() as db:
        return load_current_staff(db, read_cookie(ws, STAFF_ACCESS.name), clock=clock, path=None)


def ws_load_parent(ws: HTTPConnection, *, clock: Clock) -> CurrentParent:
    with open_session() as db:
        return load_current_parent(db, read_cookie(ws, PARENT_ACCESS.name), clock=clock)


async def run_in_thread[T](fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """同步的 DB 存取（ws_load_* / revalidate 內的查詢）放到 worker thread 執行。"""
    return await anyio.to_thread.run_sync(functools.partial(fn, *args, **kwargs))


def close_code_for(exc: AppError) -> int:
    if exc.status == 401:
        return WS_CLOSE_UNAUTHENTICATED
    if exc.status == 403:
        return WS_CLOSE_FORBIDDEN
    return WS_CLOSE_BAD_MESSAGE


def _parse_message(raw: str) -> dict[str, Any] | None:
    try:
        message = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(message, dict) or not isinstance(message.get("action"), str):
        return None
    return message


async def _close_quietly(ws: WebSocket, code: int) -> None:
    # 對方已先斷線時 close 會拋 RuntimeError
    with contextlib.suppress(RuntimeError):
        await ws.close(code=code)


async def run_ws_loop(
    ws: WebSocket,
    *,
    on_message: Callable[[dict[str, Any]], Awaitable[None]],
    revalidate: Callable[[], Awaitable[bool]],
    interval: float = REVALIDATE_SECONDS,
) -> None:
    receive_scope = anyio.CancelScope()

    async def revalidate_loop() -> None:
        while True:
            await anyio.sleep(interval)
            try:
                ok = await revalidate()
            except Exception:
                logger.warning("WebSocket 重驗時發生例外，關閉連線", exc_info=True)
                ok = False
            if not ok:
                await _close_quietly(ws, WS_CLOSE_UNAUTHENTICATED)
                receive_scope.cancel()
                return

    async def receive_loop() -> None:
        bad = 0
        while True:
            try:
                raw = await ws.receive_text()
            except WebSocketDisconnect:
                return
            message = _parse_message(raw)
            if message is None:
                bad += 1
                await ws.send_json(_BAD_MESSAGE)
                if bad >= MAX_BAD_MESSAGES:
                    await _close_quietly(ws, WS_CLOSE_BAD_MESSAGE)
                    return
                continue
            bad = 0
            if message["action"] == "ping":
                await ws.send_json(_PONG)
                continue
            await on_message(message)

    # 重驗計時器跑在子任務；收訊迴圈跑在主任務並有自己的 cancel scope（重驗失敗時由子任務取消）。
    # 子任務直接取消 task group 的 scope 會讓 __aexit__ 把 CancelledError 往外拋，故不這麼做。
    async with anyio.create_task_group() as tg:
        tg.start_soon(revalidate_loop)
        with receive_scope:
            await receive_loop()
        tg.cancel_scope.cancel()
