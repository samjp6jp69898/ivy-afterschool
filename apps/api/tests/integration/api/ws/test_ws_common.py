"""BACKEND-225：app/api/ws/common.py（WebSocket cookie 認證、關閉碼、定期重驗與訊息迴圈）。

迷你 app 的 WS 路由直接呼叫 common 的函式；DB 以 db_session 的連線取代 ``open_session``。
"""

import json
from collections.abc import Awaitable, Callable, Iterator
from typing import Any

import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from starlette.websockets import WebSocketDisconnect

from app.api.ws import common as ws_common
from app.api.ws.common import (
    MAX_BAD_MESSAGES,
    REVALIDATE_SECONDS,
    WS_CLOSE_BAD_MESSAGE,
    WS_CLOSE_FORBIDDEN,
    WS_CLOSE_UNAUTHENTICATED,
    close_code_for,
    run_ws_loop,
    ws_load_parent,
    ws_load_staff,
)
from app.core.config import get_settings
from app.core.crypto import derive_key
from app.core.errors import AppError
from app.core.security.cookies import PARENT_ACCESS, STAFF_ACCESS
from app.core.security.tokens import create_access_token
from app.core.security_middleware import WS_CLOSE_ORIGIN_FORBIDDEN
from app.models.account import StaffUser
from app.models.parents import ParentAccount
from tests.support.factories import make_parent, make_staff
from tests.support.fake_clock import FakeClock


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@127.0.0.1:54342/postgres")
    monkeypatch.setenv("APP_SECRET_KEY", "s" * 48)
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://127.0.0.1:5341")
    monkeypatch.setenv("R2_ENDPOINT_URL", "http://127.0.0.1:54344")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "afterschool")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "afterschool-local-secret")
    monkeypatch.setenv("R2_BUCKET", "afterschool-local")
    get_settings.cache_clear()
    derive_key.cache_clear()
    yield
    get_settings.cache_clear()
    derive_key.cache_clear()


@pytest.fixture
def _session_on_test_connection(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """ws_load_* 自己開的短 session 改綁測試連線，看得到測試建立（未 commit）的測資。"""

    def _open() -> Session:
        return Session(bind=db_session.connection(), join_transaction_mode="create_savepoint")

    monkeypatch.setattr(ws_common, "open_session", _open)


Revalidate = Callable[[], Awaitable[bool]]


def _build_app(
    fake_clock: FakeClock,
    *,
    revalidate: Revalidate | None = None,
    interval: float = REVALIDATE_SECONDS,
) -> FastAPI:
    app = FastAPI()
    received: list[dict[str, Any]] = []
    app.state.received = received

    async def always_ok() -> bool:
        return True

    @app.websocket("/ws/staff")
    async def staff_ws(ws: WebSocket) -> None:
        try:
            staff = await ws_common.run_in_thread(ws_load_staff, ws, clock=fake_clock)
        except AppError as exc:
            await ws.close(code=close_code_for(exc))
            return
        await ws.accept()
        await ws.send_json({"id": str(staff.id), "username": staff.username})
        await ws.close()

    @app.websocket("/ws/parent")
    async def parent_ws(ws: WebSocket) -> None:
        try:
            parent = await ws_common.run_in_thread(ws_load_parent, ws, clock=fake_clock)
        except AppError as exc:
            await ws.close(code=close_code_for(exc))
            return
        await ws.accept()
        await ws.send_json({"id": str(parent.id)})
        await ws.close()

    @app.websocket("/ws/loop")
    async def loop_ws(ws: WebSocket) -> None:
        await ws.accept()

        async def on_message(message: dict[str, Any]) -> None:
            received.append(message)
            await ws.send_json({"type": "echo", "action": message["action"]})

        await run_ws_loop(
            ws, on_message=on_message, revalidate=revalidate or always_ok, interval=interval
        )

    return app


def _staff_cookie(staff: StaffUser, clock: FakeClock) -> dict[str, str]:
    token = create_access_token(
        subject_type="staff", subject_id=staff.id, token_version=staff.token_version, clock=clock
    )
    return {"Cookie": f"{STAFF_ACCESS.name}={token}"}


def _parent_cookie(parent: ParentAccount, clock: FakeClock) -> dict[str, str]:
    token = create_access_token(
        subject_type="parent",
        subject_id=parent.id,
        token_version=parent.token_version,
        clock=clock,
    )
    return {"Cookie": f"{PARENT_ACCESS.name}={token}"}


def _connect_close_code(client: TestClient, path: str, headers: dict[str, str]) -> int:
    with (
        pytest.raises(WebSocketDisconnect) as excinfo,
        client.websocket_connect(path, headers=headers),
    ):
        pass
    return excinfo.value.code


@pytest.mark.usefixtures("_session_on_test_connection")
def test_ws_common_load_staff(db_session: Session, fake_clock: FakeClock) -> None:
    staff = make_staff(db_session, permissions=["students:read"])
    parent = make_parent(db_session)
    client = TestClient(_build_app(fake_clock))

    with client.websocket_connect("/ws/staff", headers=_staff_cookie(staff, fake_clock)) as ws:
        assert ws.receive_json() == {"id": str(staff.id), "username": staff.username}

    assert _connect_close_code(client, "/ws/staff", {}) == WS_CLOSE_UNAUTHENTICATED
    assert WS_CLOSE_UNAUTHENTICATED == 4401
    assert (
        _connect_close_code(client, "/ws/staff", {"Cookie": f"{STAFF_ACCESS.name}=garbage"})
        == WS_CLOSE_UNAUTHENTICATED
    )
    # 家長 cookie 不能上員工 WS；員工 cookie 也不能上家長 WS
    assert (
        _connect_close_code(client, "/ws/staff", _parent_cookie(parent, fake_clock))
        == WS_CLOSE_UNAUTHENTICATED
    )
    assert (
        _connect_close_code(client, "/ws/parent", _staff_cookie(staff, fake_clock))
        == WS_CLOSE_UNAUTHENTICATED
    )
    with client.websocket_connect("/ws/parent", headers=_parent_cookie(parent, fake_clock)) as ws:
        assert ws.receive_json() == {"id": str(parent.id)}
    # 直接呼叫（不經 WS）：失敗拋 AppError 供 endpoint 轉關閉碼
    with pytest.raises(AppError) as excinfo:
        ws_load_staff(_FakeConn({}), clock=fake_clock)
    assert excinfo.value.status == 401
    assert close_code_for(excinfo.value) == WS_CLOSE_UNAUTHENTICATED
    assert close_code_for(AppError("x", "y", status=409)) == WS_CLOSE_BAD_MESSAGE


class _FakeConn:
    """只提供 cookies 的最小 HTTPConnection 替身（直接呼叫 ws_load_* 用）。"""

    def __init__(self, cookies: dict[str, str]) -> None:
        self.cookies = cookies


@pytest.mark.usefixtures("_session_on_test_connection")
def test_ws_common_must_change_password_forbidden(
    db_session: Session, fake_clock: FakeClock
) -> None:
    staff = make_staff(db_session, must_change_password=True)
    client = TestClient(_build_app(fake_clock))

    code = _connect_close_code(client, "/ws/staff", _staff_cookie(staff, fake_clock))

    assert code == WS_CLOSE_FORBIDDEN
    assert WS_CLOSE_FORBIDDEN == 4403
    assert WS_CLOSE_FORBIDDEN == WS_CLOSE_ORIGIN_FORBIDDEN


def test_ws_common_ping_pong(fake_clock: FakeClock) -> None:
    app = _build_app(fake_clock)
    client = TestClient(app)

    with client.websocket_connect("/ws/loop") as ws:
        ws.send_json({"action": "ping"})
        assert ws.receive_json() == {"type": "pong"}
        # 其他 action 交給 on_message
        ws.send_json({"action": "subscribe", "channel": "pickup"})
        assert ws.receive_json() == {"type": "echo", "action": "subscribe"}
        ws.send_json({"action": "ping"})
        assert ws.receive_json() == {"type": "pong"}
    assert app.state.received == [{"action": "subscribe", "channel": "pickup"}]
    # ping 不會進 on_message


def test_ws_common_bad_message(fake_clock: FakeClock) -> None:
    assert MAX_BAD_MESSAGES == 5
    app = _build_app(fake_clock)
    client = TestClient(app)
    error = {"type": "error", "code": "bad_message"}

    with client.websocket_connect("/ws/loop") as ws:
        ws.send_text("not json")
        assert ws.receive_json() == error
        ws.send_text(json.dumps(["list", "not", "object"]))
        assert ws.receive_json() == error
        ws.send_text(json.dumps({"no_action": 1}))
        assert ws.receive_json() == error
        ws.send_text(json.dumps({"action": 123}))
        assert ws.receive_json() == error
        # 合法訊息會重設連續錯誤計數
        ws.send_json({"action": "ping"})
        assert ws.receive_json() == {"type": "pong"}
        for _ in range(4):
            ws.send_text("not json")
            assert ws.receive_json() == error
        ws.send_text("not json")
        assert ws.receive_json() == error
        with pytest.raises(WebSocketDisconnect) as excinfo:
            ws.receive_json()
        assert excinfo.value.code == WS_CLOSE_BAD_MESSAGE
        assert WS_CLOSE_BAD_MESSAGE == 4400
    assert app.state.received == []


def test_ws_common_revalidate_close(fake_clock: FakeClock) -> None:
    calls: list[int] = []

    async def revalidate() -> bool:
        calls.append(1)
        return len(calls) < 2

    app = _build_app(fake_clock, revalidate=revalidate, interval=0.1)
    client = TestClient(app)

    with client.websocket_connect("/ws/loop") as ws:
        ws.send_json({"action": "ping"})
        assert ws.receive_json() == {"type": "pong"}
        with pytest.raises(WebSocketDisconnect) as excinfo:
            ws.receive_json()
        assert excinfo.value.code == WS_CLOSE_UNAUTHENTICATED
    assert len(calls) == 2

    # revalidate 拋例外也視為失敗
    async def boom() -> bool:
        raise RuntimeError("db down")

    client = TestClient(_build_app(fake_clock, revalidate=boom, interval=0.1))
    with client.websocket_connect("/ws/loop") as ws:
        with pytest.raises(WebSocketDisconnect) as excinfo:
            ws.receive_json()
        assert excinfo.value.code == WS_CLOSE_UNAUTHENTICATED


def test_ws_common_client_disconnect_ends_loop(fake_clock: FakeClock) -> None:
    """client 斷線 → run_ws_loop 正常結束（不拋例外、不留重驗計時器）。"""
    finished: list[bool] = []
    app = FastAPI()

    async def never() -> bool:
        return True

    @app.websocket("/ws/loop")
    async def loop_ws(ws: WebSocket) -> None:
        await ws.accept()

        async def on_message(message: dict[str, Any]) -> None:
            pass

        await run_ws_loop(ws, on_message=on_message, revalidate=never, interval=0.05)
        finished.append(True)

    client = TestClient(app)
    with client.websocket_connect("/ws/loop") as ws:
        ws.send_json({"action": "ping"})
        assert ws.receive_json() == {"type": "pong"}
    assert finished == [True]
