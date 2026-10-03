"""BACKEND-051：LineIdTokenVerifier（LINE verify API、aud 嚴格比對、重放防護）。

一律以 httpx.MockTransport 模擬 LINE，不連外。
"""

import logging
import threading
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest

from app.core.errors import AppError
from app.services.auth.line_id_token import (
    HttpLineIdTokenVerifier,
    LineIdTokenVerifier,
    LineProfile,
    get_line_verifier,
)
from tests.support.fake_clock import FakeClock
from tests.support.fake_line import FakeLineVerifier

_CHANNEL = "1657000000"
_SUB = "U" + "a" * 32
_VERIFY_URL = "https://api.line.me/oauth2/v2.1/verify"
_JWT = "eyJhbGciOiJIUzI1NiJ9.fake-id-token-for-test.signature"


def _ok_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "aud": _CHANNEL,
        "sub": _SUB,
        "name": " 王媽媽 ",
        "picture": "https://p.test/a.jpg",
    }
    payload.update(overrides)
    return payload


class _Recorder:
    """MockTransport handler：記錄請求，回傳指定回應或拋指定例外。"""

    def __init__(self, respond: Callable[[httpx.Request], httpx.Response]) -> None:
        self.respond = respond
        self.requests: list[httpx.Request] = []
        self.lock = threading.Lock()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        with self.lock:
            self.requests.append(request)
        return self.respond(request)

    def forms(self) -> list[dict[str, str]]:
        return [
            {k: v[0] for k, v in parse_qs(r.content.decode(), keep_blank_values=True).items()}
            for r in self.requests
        ]


def _json(status: int, body: Any) -> Callable[[httpx.Request], httpx.Response]:
    return lambda request: httpx.Response(status, json=body)


def _verifier(recorder: _Recorder, clock: FakeClock) -> HttpLineIdTokenVerifier:
    return HttpLineIdTokenVerifier(transport=httpx.MockTransport(recorder), clock=clock)


def _app_error(exc_info: pytest.ExceptionInfo[AppError]) -> tuple[int, str]:
    return exc_info.value.status, exc_info.value.code


def _expect_error(
    verifier: HttpLineIdTokenVerifier, tok: str = _JWT, channel_id: str = _CHANNEL
) -> tuple[int, str]:
    with pytest.raises(AppError) as exc_info:
        verifier.verify(tok, channel_id=channel_id)
    return _app_error(exc_info)


# --- 成功與請求形狀 ---------------------------------------------------------------------


def test_line_verify_success(fake_clock: FakeClock) -> None:
    recorder = _Recorder(_json(200, _ok_payload()))

    profile = _verifier(recorder, fake_clock).verify("tok1", channel_id=_CHANNEL)

    assert profile == LineProfile(_SUB, "王媽媽", "https://p.test/a.jpg")
    assert recorder.forms() == [{"id_token": "tok1", "client_id": _CHANNEL}]


def test_line_verify_request_shape(fake_clock: FakeClock) -> None:
    recorder = _Recorder(_json(200, _ok_payload()))

    _verifier(recorder, fake_clock).verify("tok1", channel_id=_CHANNEL)

    (request,) = recorder.requests
    assert request.method == "POST"
    assert str(request.url) == _VERIFY_URL
    assert request.headers["content-type"] == "application/x-www-form-urlencoded"
    assert request.extensions["timeout"] == {"connect": 5.0, "read": 5.0, "write": 5.0, "pool": 5.0}


@pytest.mark.parametrize(
    ("name", "picture", "expected_name", "expected_picture"),
    [
        (None, None, None, None),
        ("   ", "", None, None),
        (123, 456, None, None),
        ("名" * 150, "https://p.test/b.png", "名" * 100, "https://p.test/b.png"),
    ],
)
def test_line_verify_profile_fields(
    fake_clock: FakeClock,
    name: Any,
    picture: Any,
    expected_name: str | None,
    expected_picture: str | None,
) -> None:
    recorder = _Recorder(_json(200, _ok_payload(name=name, picture=picture)))

    profile = _verifier(recorder, fake_clock).verify(_JWT, channel_id=_CHANNEL)

    assert profile == LineProfile(_SUB, expected_name, expected_picture)


def test_line_verify_missing_optional_profile_fields(fake_clock: FakeClock) -> None:
    recorder = _Recorder(_json(200, {"aud": _CHANNEL, "sub": _SUB}))

    assert _verifier(recorder, fake_clock).verify(_JWT, channel_id=_CHANNEL) == LineProfile(
        _SUB, None, None
    )


# --- aud / sub ------------------------------------------------------------------------


def test_line_verify_aud_mismatch(fake_clock: FakeClock) -> None:
    for aud in ("999", None, "", _CHANNEL + " ", " " + _CHANNEL):
        recorder = _Recorder(_json(200, _ok_payload(aud=aud)))
        assert _expect_error(_verifier(recorder, fake_clock)) == (401, "invalid_id_token"), aud

    no_aud = {k: v for k, v in _ok_payload().items() if k != "aud"}
    assert _expect_error(_verifier(_Recorder(_json(200, no_aud)), fake_clock)) == (
        401,
        "invalid_id_token",
    )


def test_line_verify_bad_sub(fake_clock: FakeClock) -> None:
    for sub in ("abc", None, "", "U" + "a" * 31, "U" + "a" * 33, "U" + "A" * 32, "u" + "a" * 32):
        recorder = _Recorder(_json(200, _ok_payload(sub=sub)))
        assert _expect_error(_verifier(recorder, fake_clock)) == (401, "invalid_id_token"), sub


def test_line_verify_malformed_payload(fake_clock: FakeClock) -> None:
    cases: list[Callable[[httpx.Request], httpx.Response]] = [
        lambda r: httpx.Response(200, text="not json"),
        _json(200, [_ok_payload()]),
        _json(200, "aud"),
        _json(200, _ok_payload(aud=int(_CHANNEL))),
        _json(200, _ok_payload(aud=[_CHANNEL])),
        _json(200, _ok_payload(sub=_SUB + "\n")),
        _json(200, _ok_payload(sub=12345)),
    ]
    for respond in cases:
        assert _expect_error(_verifier(_Recorder(respond), fake_clock)) == (
            401,
            "invalid_id_token",
        )


def test_line_verify_uses_given_channel_id(fake_clock: FakeClock) -> None:
    recorder = _Recorder(_json(200, _ok_payload(aud="2000000001")))
    verifier = _verifier(recorder, fake_clock)

    profile = verifier.verify("tok1", channel_id="2000000001")

    assert profile.line_user_id == _SUB
    assert recorder.forms()[0]["client_id"] == "2000000001"

    with pytest.raises(AppError) as exc_info:
        verifier.verify("tok2", channel_id="1657000000")
    assert _app_error(exc_info) == (401, "invalid_id_token")
    assert recorder.forms()[1]["client_id"] == "1657000000"


# --- LINE 錯誤回應與不可達 ----------------------------------------------------------------


def test_line_verify_non_200(fake_clock: FakeClock) -> None:
    for status in (400, 401, 403, 404, 302):
        recorder = _Recorder(_json(status, {"error": "invalid_request"}))
        assert _expect_error(_verifier(recorder, fake_clock)) == (401, "invalid_id_token"), status


def test_line_verify_unavailable(fake_clock: FakeClock) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    assert _expect_error(_verifier(_Recorder(refuse), fake_clock)) == (503, "line_unavailable")
    for status in (500, 502, 503, 504):
        recorder = _Recorder(_json(status, {"error": "server_error"}))
        assert _expect_error(_verifier(recorder, fake_clock)) == (503, "line_unavailable"), status


def test_line_verify_rate_limited_or_timeout(fake_clock: FakeClock) -> None:
    recorder = _Recorder(_json(429, {"error": "too_many_requests"}))
    assert _expect_error(_verifier(recorder, fake_clock)) == (503, "line_unavailable")

    for exc_type in (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.RemoteProtocolError):

        def fail(
            request: httpx.Request, exc_type: type[httpx.HTTPError] = exc_type
        ) -> httpx.Response:
            raise exc_type("boom", request=request)

        assert _expect_error(_verifier(_Recorder(fail), fake_clock)) == (
            503,
            "line_unavailable",
        ), exc_type


def test_line_verify_not_configured(fake_clock: FakeClock) -> None:
    recorder = _Recorder(_json(200, _ok_payload(aud="")))
    verifier = _verifier(recorder, fake_clock)

    assert _expect_error(verifier, channel_id="") == (503, "line_unavailable")
    assert _expect_error(verifier, tok="") == (401, "invalid_id_token")
    assert recorder.requests == []


# --- 重放防護 ------------------------------------------------------------------------------


def test_line_verify_replay(fake_clock: FakeClock) -> None:
    recorder = _Recorder(_json(200, _ok_payload()))
    verifier = _verifier(recorder, fake_clock)

    verifier.verify(_JWT, channel_id=_CHANNEL)
    assert _expect_error(verifier) == (401, "id_token_replayed")

    fake_clock.advance(minutes=5, seconds=1)
    assert verifier.verify(_JWT, channel_id=_CHANNEL).line_user_id == _SUB


def test_line_verify_replay_ttl_boundary(fake_clock: FakeClock) -> None:
    verifier = _verifier(_Recorder(_json(200, _ok_payload())), fake_clock)
    verifier.verify(_JWT, channel_id=_CHANNEL)

    fake_clock.advance(minutes=4, seconds=59)
    assert _expect_error(verifier) == (401, "id_token_replayed")

    # 滿 5 分鐘即過期
    fake_clock.advance(seconds=1)
    verifier.verify(_JWT, channel_id=_CHANNEL)


def test_line_verify_replay_distinct_tokens_independent(fake_clock: FakeClock) -> None:
    verifier = _verifier(_Recorder(_json(200, _ok_payload())), fake_clock)

    verifier.verify("tok-a", channel_id=_CHANNEL)
    verifier.verify("tok-b", channel_id=_CHANNEL)
    assert _expect_error(verifier, tok="tok-a") == (401, "id_token_replayed")


def test_line_verify_failed_token_not_recorded(fake_clock: FakeClock) -> None:
    payload = {"value": _ok_payload(aud="999")}
    recorder = _Recorder(lambda r: httpx.Response(200, json=payload["value"]))
    verifier = _verifier(recorder, fake_clock)

    assert _expect_error(verifier) == (401, "invalid_id_token")

    payload["value"] = _ok_payload()
    assert verifier.verify(_JWT, channel_id=_CHANNEL).line_user_id == _SUB


def test_line_verify_replay_instances_independent(fake_clock: FakeClock) -> None:
    a = _verifier(_Recorder(_json(200, _ok_payload())), fake_clock)
    b = _verifier(_Recorder(_json(200, _ok_payload())), fake_clock)

    a.verify(_JWT, channel_id=_CHANNEL)
    b.verify(_JWT, channel_id=_CHANNEL)


def test_line_verify_replay_concurrent(fake_clock: FakeClock) -> None:
    barrier = threading.Barrier(10)

    def respond(request: httpx.Request) -> httpx.Response:
        # 讓 10 個請求都先通過 LINE 驗證，再同時進入重放檢查
        barrier.wait()
        return httpx.Response(200, json=_ok_payload())

    verifier = _verifier(_Recorder(respond), fake_clock)
    results: list[str] = []
    lock = threading.Lock()

    def worker() -> None:
        try:
            verifier.verify(_JWT, channel_id=_CHANNEL)
            outcome = "ok"
        except AppError as exc:
            outcome = exc.code
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(results) == ["id_token_replayed"] * 9 + ["ok"]


# --- 其他 -----------------------------------------------------------------------------------


def test_line_verify_logs_never_contain_token(
    fake_clock: FakeClock, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    responders: list[Callable[[httpx.Request], httpx.Response]] = [
        refuse,
        _json(400, {"error": "invalid_request", "error_description": _JWT}),
        _json(200, _ok_payload(aud="999")),
        _json(200, _ok_payload(sub="bad")),
        lambda r: httpx.Response(200, text="not json " + _JWT),
    ]
    for respond in responders:
        _expect_error(_verifier(_Recorder(respond), fake_clock))
    verifier = _verifier(_Recorder(_json(200, _ok_payload())), fake_clock)
    verifier.verify(_JWT, channel_id=_CHANNEL)
    _expect_error(verifier)

    assert caplog.records, "失敗路徑應留下 warning"
    for record in caplog.records:
        assert _JWT not in record.getMessage()


def test_line_verify_error_does_not_echo_line_response(fake_clock: FakeClock) -> None:
    body = {"error": "invalid_request", "error_description": "IdToken expired."}
    with pytest.raises(AppError) as exc_info:
        _verifier(_Recorder(_json(400, body)), fake_clock).verify(_JWT, channel_id=_CHANNEL)

    assert "expired" not in exc_info.value.message
    assert exc_info.value.details is None


def test_line_verify_get_line_verifier_singleton() -> None:
    assert get_line_verifier() is get_line_verifier()
    assert isinstance(get_line_verifier(), HttpLineIdTokenVerifier)


def _verify_with(verifier: LineIdTokenVerifier, token: str) -> LineProfile:
    """只依賴 Protocol；mypy 檢查 Http / Fake 兩種實作都能傳入。"""
    return verifier.verify(token, channel_id=_CHANNEL)


def test_line_fake_verifier(fake_clock: FakeClock) -> None:
    profile = LineProfile(_SUB, "王媽媽", None)
    fake = FakeLineVerifier({"tok": profile})

    assert _verify_with(fake, "tok") == profile
    with pytest.raises(AppError) as exc_info:
        _verify_with(fake, "unknown")
    assert _app_error(exc_info) == (401, "invalid_id_token")
    assert fake.calls == [("tok", _CHANNEL), ("unknown", _CHANNEL)]

    down = FakeLineVerifier(error=AppError("line_unavailable", "x", status=503))
    with pytest.raises(AppError) as exc_info:
        _verify_with(down, "tok")
    assert _app_error(exc_info) == (503, "line_unavailable")

    assert isinstance(fake, LineIdTokenVerifier)
    http = _verifier(_Recorder(_json(200, _ok_payload())), fake_clock)
    assert _verify_with(http, _JWT).line_user_id == _SUB


def test_line_profile_is_frozen() -> None:
    profile = LineProfile(_SUB, None, None)
    with pytest.raises(AttributeError):
        profile.line_user_id = "x"  # type: ignore[misc]
    assert profile.line_user_id == _SUB
