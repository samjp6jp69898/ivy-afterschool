"""BACKEND-003：AppError 與全域 exception handlers（domain_spec §2 錯誤格式）。"""

import logging

import pytest
from app.core.errors import (
    AppError,
    NotFoundError,
    RateLimitedError,
    register_exception_handlers,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError


class _Payload(BaseModel):
    password: str
    age: int


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/app-error")
    def _app_error() -> None:
        raise AppError("leave_overlap", "請假期間重疊", status=409, details={"leave_id": "x"})

    @app.get("/not-found")
    def _not_found() -> None:
        raise NotFoundError("student_not_found", "找不到學生")

    @app.post("/validate")
    def _validate(payload: _Payload) -> dict[str, int]:
        return {"age": payload.age}

    @app.get("/integrity")
    def _integrity() -> None:
        raise IntegrityError("INSERT INTO probe ...", {}, Exception("duplicate key"))

    @app.get("/boom")
    def _boom() -> None:
        raise RuntimeError("db password=xyz")

    @app.get("/rate-limited")
    def _rate_limited() -> None:
        raise RateLimitedError("嘗試次數過多", retry_after_seconds=900)

    # 500 的例外不可由 TestClient 重新拋出，要走 handler 產生回應
    return TestClient(app, raise_server_exceptions=False)


def test_errors_app_error_envelope(client: TestClient) -> None:
    resp = client.get("/app-error")

    assert resp.status_code == 409
    assert resp.json() == {
        "error": {
            "code": "leave_overlap",
            "message": "請假期間重疊",
            "details": {"leave_id": "x"},
        }
    }


def test_errors_not_found_subclass(client: TestClient) -> None:
    resp = client.get("/not-found")

    assert resp.status_code == 404
    body = resp.json()
    assert body["error"]["code"] == "student_not_found"
    assert body["error"]["message"] == "找不到學生"
    assert body["error"]["details"] is None


def test_errors_validation_hides_input(client: TestClient) -> None:
    resp = client.post("/validate", json={"password": "Secret-123", "age": "abc"})

    assert resp.status_code == 422
    body = resp.json()
    assert body["error"]["code"] == "validation_error"
    assert "Secret-123" not in resp.text
    details = body["error"]["details"]
    assert details[0]["loc"] == ["body", "age"]
    assert set(details[0]) == {"loc", "msg", "type"}


def test_errors_unknown_route(client: TestClient) -> None:
    resp = client.get("/nope")

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"
    assert resp.json()["error"]["details"] is None


def test_errors_method_not_allowed(client: TestClient) -> None:
    resp = client.delete("/validate")

    assert resp.status_code == 405
    assert resp.json()["error"]["code"] == "method_not_allowed"
    assert resp.json()["error"]["details"] is None


def test_errors_integrity_error_to_409(client: TestClient) -> None:
    resp = client.get("/integrity")

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "conflict"
    assert resp.json()["error"]["details"] is None
    assert "duplicate key" not in resp.text
    assert "INSERT" not in resp.text


def test_errors_unhandled_500(client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.ERROR, logger="app.core.errors"):
        resp = client.get("/boom")

    assert resp.status_code == 500
    assert resp.json() == {
        "error": {"code": "internal_error", "message": "系統發生錯誤，請稍後再試", "details": None}
    }
    assert "xyz" not in resp.text
    assert "Traceback" not in resp.text
    # server 端仍要留下完整紀錄（含 request id）供追查
    assert any("db password=xyz" in rec.getMessage() for rec in caplog.records)
    assert all(
        rec.exc_info is not None for rec in caplog.records if "db password=xyz" in rec.getMessage()
    )


def test_errors_rate_limited_retry_after(client: TestClient) -> None:
    resp = client.get("/rate-limited")

    assert resp.status_code == 429
    assert resp.headers["Retry-After"] == "900"
    body = resp.json()
    assert body["error"]["code"] == "too_many_attempts"
    assert body["error"]["message"] == "嘗試次數過多"
    assert body["error"]["details"] == {"retry_after_seconds": 900}


def test_errors_subclass_defaults() -> None:
    from app.core.errors import ConflictError, ForbiddenError, UnauthenticatedError

    assert (ConflictError("dup", "重複").status, ConflictError("dup", "重複").code) == (409, "dup")
    forbidden = ForbiddenError()
    assert (forbidden.status, forbidden.code) == (403, "permission_denied")
    unauth = UnauthenticatedError()
    assert (unauth.status, unauth.code, unauth.message) == (401, "unauthenticated", "請重新登入")
