"""BACKEND-125：GET /api/parent/config（公開設定，不需登入）。"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.crypto import encrypt_token
from app.services.settings_service import clear_settings_cache

_URL = "/api/parent/config"
_TOP_KEYS = {"liff_id", "add_friend_url", "org_name", "org_phone", "logo_url", "limits"}
_LIMIT_KEYS = {
    "leave_past_days",
    "leave_future_days",
    "leave_max_attachments",
    "leave_max_attachment_mb",
    "authorization_max_days_ahead",
    "persons_max",
}
_TOKEN = "tok-public-config-9999"  # noqa: S105  測試假值


@pytest.fixture(autouse=True)
def _fresh_settings_cache() -> Iterator[None]:
    """settings 有 60 秒 in-process cache：前後清空，避免測試間互相影響。"""
    clear_settings_cache()
    yield
    clear_settings_cache()


def test_parent_config_success(api_client: TestClient) -> None:
    resp = api_client.get(_URL)

    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == _TOP_KEYS
    assert set(body["limits"]) == _LIMIT_KEYS
    assert all(isinstance(v, int) for v in body["limits"].values())
    assert isinstance(body["org_name"], str)


def test_parent_config_reflects_settings(api_client: TestClient, db_session: Session) -> None:
    db_session.execute(
        text(
            "update public.system_settings "
            "set value = jsonb_set(value, '{name}', cast(:n as jsonb)) where key = :k"
        ),
        {"n": json.dumps("陽光安親班"), "k": "org.profile"},
    )
    db_session.commit()

    resp = api_client.get(_URL)

    assert resp.json()["org_name"] == "陽光安親班"


def test_parent_config_garbage_cookie(api_client: TestClient) -> None:
    api_client.cookies.set("parent_access", "garbage")
    api_client.cookies.set("staff_access", "garbage")

    resp = api_client.get(_URL)

    assert resp.status_code == 200
    assert set(resp.json()) == _TOP_KEYS


def test_parent_config_cache_header(api_client: TestClient) -> None:
    resp = api_client.get(_URL)

    assert resp.headers["cache-control"] == "public, max-age=60"
    # 其他安全標頭仍在
    assert resp.headers["x-content-type-options"] == "nosniff"
    # 其他 /api 路徑維持 no-store
    assert api_client.get("/api/health").headers["cache-control"] == "no-store"


def test_parent_config_no_secret(api_client: TestClient, db_session: Session) -> None:
    db_session.execute(
        text("update public.system_settings set value = cast(:v as jsonb) where key = :k"),
        {
            "v": json.dumps(
                {"channel_access_token": encrypt_token(_TOKEN), "channel_secret": None}
            ),
            "k": "line.messaging",
        },
    )
    db_session.commit()

    resp = api_client.get(_URL)

    assert _TOKEN not in resp.text
    assert "****" not in resp.text
    assert "channel_" not in resp.text


def test_parent_config_method_not_allowed(
    api_client: TestClient, assert_error: Callable[..., None]
) -> None:
    assert_error(api_client.post(_URL), 405, "method_not_allowed")
