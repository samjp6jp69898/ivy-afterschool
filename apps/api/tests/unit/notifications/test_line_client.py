"""BACKEND-207：app/notifications/channels/line.py（LINE Messaging push 用戶端）。
BACKEND-543：close() 與 context manager。

一律以 httpx.MockTransport 模擬 LINE，不連外；build_line_client 以 monkeypatch 的 get_setting 取代
DB 讀取。
"""

import json
from collections.abc import Callable
from typing import Any
from uuid import UUID

import httpx
import pytest

from app.core.settings_registry import LINE_MESSAGING, LineMessaging, SettingKey
from app.notifications.channels import line as line_module
from app.notifications.channels.line import (
    LINE_PUSH_URL,
    HttpLineMessagingClient,
    LineMessagingClient,
    LineSendResult,
    build_line_client,
)

_TOKEN = "tok-1"  # noqa: S105  測試假值
_TO = "U" + "a" * 32
_RETRY_KEY = UUID(int=7)


class _Recorder:
    """MockTransport handler：記錄請求，回傳指定回應或拋指定例外。"""

    def __init__(self, respond: Callable[[httpx.Request], httpx.Response]) -> None:
        self.respond = respond
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.respond(request)


def _json(status: int, body: Any) -> Callable[[httpx.Request], httpx.Response]:
    return lambda request: httpx.Response(status, json=body)


def _raise(exc: Exception) -> Callable[[httpx.Request], httpx.Response]:
    def _handler(request: httpx.Request) -> httpx.Response:
        raise exc

    return _handler


def _client(recorder: _Recorder) -> HttpLineMessagingClient:
    return HttpLineMessagingClient(_TOKEN, transport=httpx.MockTransport(recorder))


def _push(respond: Callable[[httpx.Request], httpx.Response]) -> tuple[LineSendResult, _Recorder]:
    recorder = _Recorder(respond)
    result = _client(recorder).push_text(to=_TO, text="hi", retry_key=_RETRY_KEY)
    return result, recorder


def test_line_client_request() -> None:
    result, recorder = _push(_json(200, {}))

    assert result == LineSendResult(ok=True, retryable=False, error=None)
    assert len(recorder.requests) == 1
    request = recorder.requests[0]
    assert request.method == "POST"
    assert str(request.url) == LINE_PUSH_URL == "https://api.line.me/v2/bot/message/push"
    assert request.headers["authorization"] == "Bearer tok-1"
    assert request.headers["x-line-retry-key"] == "00000000-0000-0000-0000-000000000007"
    assert request.headers["content-type"] == "application/json"
    body = json.loads(request.content)
    assert body == {"to": _TO, "messages": [{"type": "text", "text": "hi"}]}
    assert body["messages"][0] == {"type": "text", "text": "hi"}
    # 中文與多行文字原樣送出
    recorder2 = _Recorder(_json(200, {}))
    _client(recorder2).push_text(to=_TO, text="王小明 已到班\n請到櫃台", retry_key=_RETRY_KEY)
    assert json.loads(recorder2.requests[0].content)["messages"][0]["text"] == (
        "王小明 已到班\n請到櫃台"
    )


def test_line_client_409_is_ok() -> None:
    result, _ = _push(_json(409, {"message": "The retry key is already accepted"}))

    assert result.ok is True
    assert result.retryable is False
    assert result.error is None


def test_line_client_retryable() -> None:
    result, _ = _push(_json(429, {"message": "Too many requests"}))
    assert (result.ok, result.retryable) == (False, True)
    assert result.error is not None

    assert "429" in result.error

    result, _ = _push(_json(503, {"message": "Service Unavailable"}))
    assert (result.ok, result.retryable) == (False, True)
    assert result.error is not None

    assert "503" in result.error

    result, _ = _push(_json(500, {}))
    assert (result.ok, result.retryable) == (False, True)

    result, _ = _push(_raise(httpx.ReadTimeout("timed out")))
    assert (result.ok, result.retryable) == (False, True)
    assert result.error is not None

    assert "ReadTimeout" in result.error

    result, _ = _push(_raise(httpx.ConnectError("connection refused")))
    assert (result.ok, result.retryable) == (False, True)
    assert result.error is not None

    assert "ConnectError" in result.error


def test_line_client_permanent() -> None:
    result, _ = _push(_json(400, {"message": "Invalid reply token"}))
    assert (result.ok, result.retryable) == (False, False)
    assert result.error is not None
    assert "400" in result.error
    assert "Invalid reply token" in result.error
    assert _TOKEN not in result.error

    for status in (401, 403, 404):
        result, _ = _push(_json(status, {"message": f"error {status}"}))
        assert (result.ok, result.retryable) == (False, False), status
        assert result.error is not None

        assert str(status) in result.error


def test_line_client_error_truncated_and_token_free() -> None:
    long_body = {"message": "x" * 5000, "echo": f"Bearer {_TOKEN}"}
    result, _ = _push(_json(400, long_body))

    assert result.ok is False
    assert result.error is not None
    assert len(result.error) <= 2000
    assert _TOKEN not in result.error

    # 例外訊息也不帶 token
    result, _ = _push(_raise(httpx.ConnectError(f"refused for Bearer {_TOKEN}")))
    assert result.error is not None
    assert _TOKEN not in result.error


def test_line_client_build_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def fake_get_setting(session: Any, key: SettingKey[Any]) -> Any:
        seen.append(key.key)
        return LineMessaging(channel_access_token=None, channel_secret="sec")  # noqa: S106

    monkeypatch.setattr(line_module, "get_setting", fake_get_setting)
    assert build_line_client(object()) is None  # type: ignore[arg-type]
    assert seen == [LINE_MESSAGING.key]

    monkeypatch.setattr(
        line_module,
        "get_setting",
        lambda session, key: LineMessaging(channel_access_token=_TOKEN, channel_secret=None),
    )
    client = build_line_client(object())  # type: ignore[arg-type]
    assert isinstance(client, HttpLineMessagingClient)
    # 結構相容 Protocol：靜態型別檢查把關，這裡只確認方法存在
    typed: LineMessagingClient = client
    assert callable(typed.push_text)


# --- BACKEND-543：close() 與 context manager -----------------------------------------------------


def _http_client(recorder: _Recorder) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(recorder))


def test_line_client_close_closes_http_client() -> None:
    recorder = _Recorder(_json(200, {}))
    http_client = _http_client(recorder)
    client = HttpLineMessagingClient(_TOKEN, http_client=http_client)
    # 注入的 client 照常可推播（Authorization 由本類別補上）
    assert client.push_text(to=_TO, text="hi", retry_key=_RETRY_KEY).ok is True
    assert recorder.requests[0].headers["authorization"] == "Bearer tok-1"
    assert http_client.is_closed is False

    client.close()

    assert http_client.is_closed is True
    client.close()  # 冪等
    assert http_client.is_closed is True


def test_line_client_context_manager() -> None:
    http_client = _http_client(_Recorder(_json(200, {})))
    outer = HttpLineMessagingClient(_TOKEN, http_client=http_client)

    with outer as c:
        assert c is outer
        assert http_client.is_closed is False
        assert c.push_text(to=_TO, text="hi", retry_key=_RETRY_KEY).ok is True

    assert http_client.is_closed is True


def test_line_client_push_after_close_raises() -> None:
    client = _client(_Recorder(_json(200, {})))
    client.close()

    with pytest.raises(RuntimeError) as exc:
        client.push_text(to=_TO, text="hi", retry_key=_RETRY_KEY)

    assert _TOKEN not in str(exc.value)
    assert "關閉" in str(exc.value)


def test_line_client_http_client_and_transport_exclusive() -> None:
    http_client = _http_client(_Recorder(_json(200, {})))

    with pytest.raises(ValueError, match="http_client"):
        HttpLineMessagingClient(
            _TOKEN, transport=httpx.MockTransport(_json(200, {})), http_client=http_client
        )
