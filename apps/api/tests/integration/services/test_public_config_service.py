"""BACKEND-124：app/services/public_config_service.py（家長端公開設定 get_public_config）。

設定值在 db_session 內以 jsonb 合併更新、測試結束 rollback；secret 欄位以 encrypt_token 寫入密文。
"""

import json
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.crypto import derive_key, encrypt_token
from app.core.settings_registry import REGISTRY
from app.services.public_config_service import (
    PUBLIC_SOURCES,
    PublicConfigOut,
    PublicLimitsOut,
    get_public_config,
)
from app.services.settings_service import clear_settings_cache, invalidate_setting

_TOKEN = "tok-123"  # noqa: S105  測試假值


@pytest.fixture(autouse=True)
def _env_and_cache(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
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
    clear_settings_cache()
    yield
    clear_settings_cache()
    get_settings.cache_clear()
    derive_key.cache_clear()


def _put(session: Session, key: str, patch: dict[str, Any]) -> None:
    """jsonb 合併更新指定 key 的部分欄位，並讓 cache 失效。"""
    session.execute(
        text("update public.system_settings set value = value || cast(:v as jsonb) where key = :k"),
        {"v": json.dumps(patch), "k": key},
    )
    invalidate_setting(key)


def test_public_config_values(db_session: Session) -> None:
    _put(db_session, "org.profile", {"name": "快樂安親班", "phone": "02-2345-0000"})
    _put(db_session, "line.liff", {"liff_id": "1657000000-Abc"})

    config = get_public_config(db_session)

    assert config == PublicConfigOut(
        liff_id="1657000000-Abc",
        add_friend_url=None,
        org_name="快樂安親班",
        org_phone="02-2345-0000",
        logo_url=None,
        limits=PublicLimitsOut(
            leave_past_days=30,
            leave_future_days=60,
            leave_max_attachments=3,
            leave_max_attachment_mb=10,
            authorization_max_days_ahead=14,
            persons_max=10,
        ),
    )
    # 設定更新並失效 cache 後立即反映；logo_url 轉成字串
    _put(
        db_session,
        "org.profile",
        {"name": "陽光安親班", "logo_url": "https://cdn.example.com/logo.png"},
    )
    again = get_public_config(db_session)
    assert again.org_name == "陽光安親班"
    assert again.logo_url == "https://cdn.example.com/logo.png"
    assert again.org_phone == "02-2345-0000"


def test_public_config_no_secret(db_session: Session) -> None:
    _put(
        db_session,
        "line.messaging",
        {"channel_access_token": encrypt_token(_TOKEN), "channel_secret": encrypt_token("sec-9")},
    )
    _put(db_session, "line.liff", {"channel_id": "1234567890", "liff_id": "1657000000-Abc"})

    dumped = json.dumps(get_public_config(db_session).model_dump(), ensure_ascii=False)

    assert _TOKEN not in dumped
    assert "sec-9" not in dumped
    assert "v1:" not in dumped
    assert "1234567890" not in dumped
    assert "channel" not in dumped
    assert set(PublicConfigOut.model_fields) == {
        "liff_id",
        "add_friend_url",
        "org_name",
        "org_phone",
        "logo_url",
        "limits",
    }


def test_public_config_fields_are_registry_public() -> None:
    """輸出欄位只能來自 registry 標記 public_fields 的欄位，且絕不是 secret 欄位。"""
    assert set(PUBLIC_SOURCES.values()) == {
        ("org.profile", "name"),
        ("org.profile", "phone"),
        ("org.profile", "logo_url"),
        ("line.liff", "liff_id"),
        ("line.liff", "add_friend_url"),
        ("leave.window", "past_days"),
        ("leave.window", "future_days"),
        ("leave.window", "max_attachments"),
        ("leave.window", "max_attachment_mb"),
        ("pickup.authorization", "max_days_ahead"),
        ("pickup.persons", "max_per_student"),
    }
    for key, field in PUBLIC_SOURCES.values():
        definition = REGISTRY[key]
        assert field in definition.public_fields, (key, field)
        assert field not in definition.secret_fields, (key, field)
        assert definition.is_secret is False, key
    assert "line.messaging" not in {key for key, _ in PUBLIC_SOURCES.values()}


def test_public_config_limits_follow_settings(db_session: Session) -> None:
    _put(db_session, "leave.window", {"max_attachments": 5, "past_days": 7})
    _put(db_session, "pickup.persons", {"max_per_student": 4})
    _put(db_session, "pickup.authorization", {"max_days_ahead": 30})
    _put(db_session, "line.liff", {"add_friend_url": "https://lin.ee/abc", "channel_id": "987654"})

    config = get_public_config(db_session)

    assert config.limits.leave_max_attachments == 5
    assert config.limits.leave_past_days == 7
    assert config.limits.leave_future_days == 60
    assert config.limits.persons_max == 4
    assert config.limits.authorization_max_days_ahead == 30
    assert config.add_friend_url == "https://lin.ee/abc"
    assert "987654" not in json.dumps(config.model_dump())
    assert not hasattr(config, "channel_id")
