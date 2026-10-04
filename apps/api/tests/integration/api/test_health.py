"""BACKEND-021：GET /api/health（存活與 DB 連線檢查）。"""

from __future__ import annotations

import logging
from collections.abc import Callable, Generator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.db import get_db

_BROKEN_URL = "postgresql+psycopg://x:y@127.0.0.1:1/none"


def test_health_ok(api_client: TestClient) -> None:
    resp = api_client.get("/api/health")

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "app_name": "afterschool-api", "db": "ok"}


def test_health_db_down_503(app: FastAPI, api_client: TestClient) -> None:
    def _broken_db() -> Generator[Session]:
        engine = create_engine(_BROKEN_URL)
        session = Session(bind=engine)
        try:
            yield session
        finally:
            session.close()
            engine.dispose()

    app.dependency_overrides[get_db] = _broken_db

    resp = api_client.get("/api/health")

    assert resp.status_code == 503
    assert resp.json() == {"status": "degraded", "app_name": "afterschool-api", "db": "error"}
    assert "127.0.0.1:1" not in resp.text
    assert "postgresql" not in resp.text


def test_health_ignores_auth_cookie_garbage(api_client: TestClient) -> None:
    api_client.cookies.set("staff_access", "garbage")
    api_client.cookies.set("parent_access", "garbage")

    resp = api_client.get("/api/health")

    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_health_method_not_allowed(
    api_client: TestClient, assert_error: Callable[..., None]
) -> None:
    resp = api_client.post("/api/health")

    assert_error(resp, 405, "method_not_allowed")


def test_health_no_access_log(api_client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="app.access"):
        api_client.get("/api/health")
        health_records = [r for r in caplog.records if r.name == "app.access"]
        api_client.get("/api/__no_such_route")
        all_records = [r for r in caplog.records if r.name == "app.access"]

    assert health_records == []
    # 對照組：其他路徑會寫 access log，證明上面的空清單不是因為 caplog 沒捕捉到
    assert len(all_records) == 1
