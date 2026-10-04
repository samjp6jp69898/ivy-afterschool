"""BACKEND-020：app/main.py（create_app 工廠、lifespan、middleware、router 彙整、Sentry 遮罩）。"""

import json
import logging
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import main as main_module
from app.api.admin import admin_router
from app.api.parent import parent_router
from app.core import logging as app_logging
from app.core.config import Settings
from app.core.logging import scrub_sentry_breadcrumb, scrub_sentry_event
from app.main import create_app
from app.realtime.broadcaster import reset_broadcaster_for_tests

_LOCAL_R2_SECRET = "afterschool-local-secret"  # noqa: S105  本機 SeaweedFS 固定開發值
_ORIGIN = "http://127.0.0.1:5341"


def _settings(app_env: str = "test", cors_origins: str = "") -> Settings:
    cloud = app_env == "production"
    return Settings(
        _env_file=None,
        app_env=app_env,
        database_url="postgresql+psycopg://u:p@127.0.0.1:54342/postgres",
        app_secret_key="s" * 48,
        cors_origins=cors_origins,
        public_base_url=_ORIGIN,
        r2_endpoint_url=(
            "https://acct.r2.cloudflarestorage.com" if cloud else "http://127.0.0.1:54344"
        ),
        r2_access_key_id="afterschool",
        r2_secret_access_key="k" * 40 if cloud else _LOCAL_R2_SECRET,
        r2_bucket="afterschool-local",
    )


@pytest.fixture(autouse=True)
def restore_global_state() -> Iterator[None]:
    """lifespan 會改 root logger 與 broadcaster 單例；測試結束還原。"""
    root = logging.getLogger()
    handlers = list(root.handlers)
    level = root.level
    yield
    for handler in list(root.handlers):
        if handler not in handlers:
            root.removeHandler(handler)
    for handler in handlers:
        if handler not in root.handlers:
            root.addHandler(handler)
    root.setLevel(level)
    reset_broadcaster_for_tests()


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, *args: Any, **kwargs: Any) -> object:
        self.calls.append({"args": args, "kwargs": kwargs})
        return object()


@pytest.fixture
def scheduler_spies(monkeypatch: pytest.MonkeyPatch) -> tuple[_Recorder, _Recorder]:
    start, shutdown = _Recorder(), _Recorder()
    monkeypatch.setattr(main_module, "start_scheduler", start)
    monkeypatch.setattr(main_module, "shutdown_scheduler", shutdown)
    return start, shutdown


# --- lifespan ---------------------------------------------------------------------------


def test_main_create_app_test_env_no_scheduler(
    scheduler_spies: tuple[_Recorder, _Recorder],
) -> None:
    start, shutdown = scheduler_spies
    app = create_app(settings=_settings("test"))

    assert isinstance(app, FastAPI)
    with TestClient(app) as client:
        assert client.get("/api/does-not-exist").status_code == 404
        assert start.calls == []
    assert start.calls == []
    assert shutdown.calls == []


def test_main_start_background_starts_and_stops(
    scheduler_spies: tuple[_Recorder, _Recorder],
) -> None:
    start, shutdown = scheduler_spies
    app = create_app(settings=_settings("test"), start_background=True)

    with TestClient(app):
        assert len(start.calls) == 1
        assert shutdown.calls == []
        kwargs = start.calls[0]["kwargs"]
        assert callable(kwargs["session_factory"])
        assert hasattr(kwargs["clock"], "now")
    assert len(start.calls) == 1
    assert len(shutdown.calls) == 1


# --- docs / envelope / cors -------------------------------------------------------------


def test_main_docs_disabled_in_production(scheduler_spies: tuple[_Recorder, _Recorder]) -> None:
    production = TestClient(create_app(settings=_settings("production")))
    assert production.get("/api/docs").status_code == 404
    assert production.get("/openapi.json").status_code == 404
    assert production.get("/api/openapi.json").status_code == 404
    assert production.get("/docs").status_code == 404

    development = TestClient(create_app(settings=_settings("development")))
    assert development.get("/api/docs").status_code == 200
    assert development.get("/api/openapi.json").status_code == 200


def test_main_error_envelope_wired(scheduler_spies: tuple[_Recorder, _Recorder]) -> None:
    client = TestClient(create_app(settings=_settings("test")))

    response = client.get("/api/does-not-exist")

    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "not_found"
    assert set(body["error"]) == {"code", "message", "details"}
    assert response.headers["X-Request-ID"]
    assert response.headers["x-content-type-options"] == "nosniff"


def test_main_cors_preflight(scheduler_spies: tuple[_Recorder, _Recorder]) -> None:
    client = TestClient(create_app(settings=_settings("test", cors_origins=_ORIGIN)))

    response = client.options(
        "/api/health",
        headers={"Origin": _ORIGIN, "Access-Control-Request-Method": "GET"},
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == _ORIGIN
    assert response.headers["access-control-allow-credentials"] == "true"
    assert "GET" in response.headers["access-control-allow-methods"]

    denied = client.options(
        "/api/health",
        headers={"Origin": "https://evil.test", "Access-Control-Request-Method": "GET"},
    )
    assert "access-control-allow-origin" not in denied.headers

    # cors_origins 為空時不掛 CORSMiddleware：preflight 沒有 CORS 標頭
    no_cors = TestClient(create_app(settings=_settings("test")))
    plain = no_cors.options(
        "/api/health", headers={"Origin": _ORIGIN, "Access-Control-Request-Method": "GET"}
    )
    assert "access-control-allow-origin" not in plain.headers


# --- routers ----------------------------------------------------------------------------


def test_main_routers_mounted(scheduler_spies: tuple[_Recorder, _Recorder]) -> None:
    """彙整 router 先掛探針路由再 create_app，確認 /api/admin 與 /api/parent 前綴被 include。"""

    @admin_router.get("/probe-admin")
    def _admin_probe() -> dict[str, str]:
        return {"ok": "admin"}

    @parent_router.get("/probe-parent")
    def _parent_probe() -> dict[str, str]:
        return {"ok": "parent"}

    try:
        app = create_app(settings=_settings("test"))
        paths = {getattr(r, "path", "") for r in app.routes}
        assert "/api/admin/probe-admin" in paths
        assert "/api/parent/probe-parent" in paths
        assert any(p.startswith("/api/admin") for p in paths)
        assert any(p.startswith("/api/parent") for p in paths)
        client = TestClient(app)
        assert client.get("/api/admin/probe-admin").json() == {"ok": "admin"}
        assert client.get("/api/parent/probe-parent").json() == {"ok": "parent"}
    finally:
        admin_router.routes[:] = [
            r for r in admin_router.routes if getattr(r, "path", "") != "/api/admin/probe-admin"
        ]
        parent_router.routes[:] = [
            r for r in parent_router.routes if getattr(r, "path", "") != "/api/parent/probe-parent"
        ]


# --- 未處理例外的 500 ---------------------------------------------------------------------


def test_main_unhandled_500_has_request_id_and_security_headers(
    scheduler_spies: tuple[_Recorder, _Recorder],
) -> None:
    app = create_app(settings=_settings("test"))

    @app.get("/api/boom")
    def _boom() -> None:
        raise RuntimeError("boom password=PW7")

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/api/boom", headers={"X-Request-ID": "req-12345678"})

    assert response.status_code == 500
    assert response.headers["X-Request-ID"] == "req-12345678"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "same-origin"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["error"]["code"] == "internal_error"
    assert "PW7" not in response.text

    # 沒帶 X-Request-ID 時由 middleware 產生，500 回應仍帶回同一個
    generated = client.get("/api/boom")
    assert generated.status_code == 500
    assert len(generated.headers["X-Request-ID"]) == 32


# --- Sentry 遮罩 ------------------------------------------------------------------------


def test_main_sentry_scrubs_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    event = {
        "message": "raw token=TK8",
        "logentry": {"message": "login token=TK9", "params": ["secret=S1", {"password": "P2"}]},
        "exception": {
            "values": [
                {
                    "type": "RuntimeError",
                    "value": "Authorization: Bearer abc.def",
                    "stacktrace": {"frames": [{"filename": "x.py", "vars": {"token": "T3"}}]},
                }
            ]
        },
        "extra": {"cookie": "sid=C4", "user": "amy"},
        "request": {
            "url": "https://example.test/api/x",
            "query_string": "code=Q5&page=2",
            "headers": {"Authorization": "Bearer x", "Cookie": "staff_access=y", "Accept": "*/*"},
            "cookies": {"staff_access": "y2"},
        },
        "breadcrumbs": {"values": [{"message": "refresh_token=R6"}]},
    }

    scrubbed = scrub_sentry_event(event, {})

    assert scrubbed is not None
    text = json.dumps(scrubbed, ensure_ascii=False)
    for secret in (
        "TK8",
        "TK9",
        "S1",
        "P2",
        "abc.def",
        "Bearer x",
        "staff_access=y",
        "y2",
        "T3",
        "C4",
        "Q5",
        "R6",
    ):
        assert secret not in text, secret
    assert scrubbed["exception"]["values"][0]["type"] == "RuntimeError"
    assert scrubbed["exception"]["values"][0]["stacktrace"]["frames"][0]["filename"] == "x.py"
    assert scrubbed["extra"]["user"] == "amy"
    assert scrubbed["request"]["headers"]["Accept"] == "*/*"
    assert "page=2" in scrubbed["request"]["query_string"]
    # 原 event 未被就地修改
    assert event["message"] == "raw token=TK8"

    crumb = scrub_sentry_breadcrumb(
        {"message": "refresh_token=R2", "data": {"password": "P3", "n": 1}}, {}
    )
    assert crumb is not None
    crumb_text = json.dumps(crumb)
    assert "R2" not in crumb_text
    assert "P3" not in crumb_text
    assert crumb["data"]["n"] == 1

    def _boom(value: str) -> str:
        raise RuntimeError("redact 爆炸")

    monkeypatch.setattr(app_logging, "redact_text", _boom)
    assert scrub_sentry_event({"message": "token=TK1"}, {}) is None
    assert scrub_sentry_breadcrumb({"message": "token=TK1"}, {}) is None
