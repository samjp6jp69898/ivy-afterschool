"""BACKEND-004：app/core/logging.py（logging 設定、request id middleware、敏感欄位遮罩）。"""

import json
import logging
import re
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.logging import (
    ACCESS_LOGGER_NAME,
    RedactingFilter,
    RequestContextMiddleware,
    configure_logging,
    request_id_var,
)

_HEX32 = re.compile(r"^[0-9a-f]{32}$")
_SECRET = "s" * 48


def _settings(app_env: str) -> Settings:
    cloud = app_env == "production"
    return Settings(
        _env_file=None,
        app_env=app_env,  # type: ignore[arg-type]
        database_url="postgresql+psycopg://u:p@127.0.0.1:54342/postgres",
        app_secret_key=_SECRET,
        public_base_url="http://127.0.0.1:5341",  # type: ignore[arg-type]
        r2_endpoint_url=(  # type: ignore[arg-type]
            "https://acct.r2.cloudflarestorage.com" if cloud else "http://127.0.0.1:54344"
        ),
        r2_access_key_id="afterschool",
        r2_secret_access_key="k" * 40 if cloud else "afterschool-local-secret",
        r2_bucket="afterschool-local",
    )


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)

    @app.get("/ping")
    def _ping() -> dict[str, str | None]:
        return {"request_id": request_id_var.get()}

    @app.get("/api/health")
    def _health() -> dict[str, str]:
        return {"status": "ok"}

    return TestClient(app)


@pytest.fixture
def restore_root_logging() -> Iterator[None]:
    """configure_logging 會改 root logger；測試結束還原，避免影響其他測試。"""
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


def _access_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == ACCESS_LOGGER_NAME]


# --- request id -------------------------------------------------------------------------


def test_logging_request_id_generated(client: TestClient) -> None:
    response = client.get("/ping")

    assert response.status_code == 200
    request_id = response.headers["X-Request-ID"]
    assert _HEX32.match(request_id)
    # handler 內透過 contextvar 拿到同一個 id
    assert response.json() == {"request_id": request_id}
    # 請求結束後 contextvar 已清空
    assert request_id_var.get() is None


def test_logging_request_id_propagated(client: TestClient) -> None:
    good = client.get("/ping", headers={"X-Request-ID": "abcd1234-req"})
    assert good.headers["X-Request-ID"] == "abcd1234-req"
    assert good.json() == {"request_id": "abcd1234-req"}

    bad = client.get("/ping", headers={"X-Request-ID": "bad id!"})
    assert bad.headers["X-Request-ID"] != "bad id!"
    assert _HEX32.match(bad.headers["X-Request-ID"])

    too_short = client.get("/ping", headers={"X-Request-ID": "abc"})
    assert _HEX32.match(too_short.headers["X-Request-ID"])


# --- access log -------------------------------------------------------------------------


def test_logging_access_log_no_query(client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO):
        response = client.get("/ping?code=SECRET88")

    assert response.status_code == 200
    records = _access_records(caplog)
    assert len(records) == 1
    message = records[0].getMessage()
    assert "GET" in message
    assert "/ping" in message
    assert "200" in message
    assert "SECRET88" not in caplog.text
    assert "code=" not in caplog.text


def test_logging_health_not_logged(client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO):
        response = client.get("/api/health")

    assert response.status_code == 200
    assert response.headers["X-Request-ID"]
    assert _access_records(caplog) == []


def test_logging_access_log_status_on_handler_error(caplog: pytest.LogCaptureFixture) -> None:
    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)

    @app.get("/boom")
    def _boom() -> None:
        raise RuntimeError("boom")

    with caplog.at_level(logging.INFO):
        response = TestClient(app, raise_server_exceptions=False).get("/boom")

    assert response.status_code == 500
    records = _access_records(caplog)
    assert len(records) == 1
    assert "500" in records[0].getMessage()
    assert request_id_var.get() is None


# --- RedactingFilter --------------------------------------------------------------------


def _filtered_message(msg: str, *args: object) -> str:
    record = logging.LogRecord("t", logging.INFO, __file__, 1, msg, args, None)
    assert RedactingFilter().filter(record) is True
    return record.getMessage()


def test_logging_redacting_filter() -> None:
    message = _filtered_message("login %s", {"username": "amy", "password": "P@ss-1234"})

    assert "amy" in message
    assert "***" in message
    assert "P@ss-1234" not in message


def test_logging_redacting_filter_nested_and_literal() -> None:
    nested = _filtered_message(
        "line %s %s",
        {"profile": {"id_token": "eyJ-tok", "name": "王小明"}},
        {"headers": [{"cookie": "sid=abc"}]},
    )
    assert "eyJ-tok" not in nested
    assert "sid=abc" not in nested
    assert "王小明" in nested

    literal = _filtered_message("bind code=ABC123 token=xyz.1 status_code=200 user=amy")
    assert "ABC123" not in literal
    assert "xyz.1" not in literal
    assert "status_code=200" in literal
    assert "user=amy" in literal

    quoted = _filtered_message("payload {'authorization': 'Bearer abc', 'secret': \"s3\"}")
    assert "Bearer abc" not in quoted
    assert "s3" not in quoted


# --- configure_logging ------------------------------------------------------------------


@pytest.mark.usefixtures("restore_root_logging")
def test_logging_production_json(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(_settings("production"))
    token = request_id_var.set("req-json-0001")
    try:
        logging.getLogger("app.test").info("hello %s", {"password": "P@ss-1234", "n": 1})
    finally:
        request_id_var.reset(token)
    logging.getLogger("app.test").debug("not shown")

    lines = [line for line in capsys.readouterr().err.splitlines() if line.strip()]
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert {"ts", "level", "logger", "msg", "request_id"} <= set(entry)
    assert entry["level"] == "INFO"
    assert entry["logger"] == "app.test"
    assert entry["request_id"] == "req-json-0001"
    assert "P@ss-1234" not in lines[0]
    assert "***" in entry["msg"]


@pytest.mark.usefixtures("restore_root_logging")
def test_logging_development_human_readable(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(_settings("development"))
    configure_logging(_settings("development"))  # 重複呼叫不會重複輸出
    logging.getLogger("app.test").info("hello %s", {"token": "t-1"})

    lines = [line for line in capsys.readouterr().err.splitlines() if line.strip()]
    assert len(lines) == 1
    with pytest.raises(json.JSONDecodeError):
        json.loads(lines[0])
    assert "INFO" in lines[0]
    assert "app.test" in lines[0]
    assert "t-1" not in lines[0]
    assert logging.getLogger().level == logging.INFO
    assert logging.getLogger("uvicorn.access").disabled is True
