"""BACKEND-108：app/services/settings_service.py（get_setting、invalidate_setting、clear_settings_cache）。

DB 的變更都在 db_session 的 transaction 內，測試結束 rollback，不影響 seed。
"""

import json
import logging
import time
from collections.abc import Iterator
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.crypto import derive_key, encrypt_token
from app.core.settings_registry import (
    HOMEWORK_DEFAULTS,
    LINE_MESSAGING,
    ORG_SERVICE_HOURS,
    PICKUP_WINDOW,
    HomeworkDefaults,
    PickupWindow,
    ServiceHours,
)
from app.services import settings_service
from app.services.settings_service import (
    SETTINGS_CACHE_TTL_SECONDS,
    clear_settings_cache,
    get_setting,
    invalidate_setting,
)

_LOGGER = "app.services.settings_service"
_TOKEN = "tok-123"  # noqa: S105  測試假值
_CHANNEL_SECRET = "sec-456"  # noqa: S105  測試假值


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


class _Monotonic:
    """真實 monotonic 加上可控的位移，只讓時間前進，不影響其他元件。"""

    def __init__(self) -> None:
        self._real = time.monotonic
        self.offset = 0.0

    def __call__(self) -> float:
        return self._real() + self.offset


@pytest.fixture
def monotonic(monkeypatch: pytest.MonkeyPatch) -> _Monotonic:
    clock = _Monotonic()
    monkeypatch.setattr(time, "monotonic", clock)
    return clock


def _set_value(session: Session, key: str, value: dict[str, Any]) -> None:
    session.execute(
        text("update public.system_settings set value = cast(:v as jsonb) where key = :k"),
        {"v": json.dumps(value), "k": key},
    )


def _set_auto_expire(session: Session, minutes: int) -> None:
    session.execute(
        text(
            "update public.system_settings "
            "set value = jsonb_set(value, '{auto_expire_minutes}', to_jsonb(cast(:m as int))) "
            "where key = 'pickup.window'"
        ),
        {"m": minutes},
    )


def test_get_setting_typed(db_session: Session) -> None:
    window = get_setting(db_session, PICKUP_WINDOW)

    assert isinstance(window, PickupWindow)
    assert window.auto_expire_minutes == 120
    assert (window.request_start, window.request_end) == ("12:00", "19:00")
    hours = get_setting(db_session, ORG_SERVICE_HOURS)
    assert isinstance(hours, ServiceHours)
    assert hours.mon.open is True
    assert hours.sat.open is False
    # frozen：呼叫端不可修改（快取中的實例是共用的）
    with pytest.raises(ValidationError):
        window.auto_expire_minutes = 30  # type: ignore[misc]


def test_get_setting_cache_ttl(db_session: Session, monotonic: _Monotonic) -> None:
    assert SETTINGS_CACHE_TTL_SECONDS == 60
    assert get_setting(db_session, PICKUP_WINDOW).auto_expire_minutes == 120

    _set_auto_expire(db_session, 90)
    assert get_setting(db_session, PICKUP_WINDOW).auto_expire_minutes == 120

    monotonic.offset = 59
    assert get_setting(db_session, PICKUP_WINDOW).auto_expire_minutes == 120

    monotonic.offset = 61
    assert get_setting(db_session, PICKUP_WINDOW).auto_expire_minutes == 90


def test_get_setting_invalidate(db_session: Session) -> None:
    assert get_setting(db_session, PICKUP_WINDOW).auto_expire_minutes == 120
    _set_auto_expire(db_session, 90)

    # 只失效指定的 key
    invalidate_setting("org.profile")
    assert get_setting(db_session, PICKUP_WINDOW).auto_expire_minutes == 120

    invalidate_setting("pickup.window")
    assert get_setting(db_session, PICKUP_WINDOW).auto_expire_minutes == 90

    _set_auto_expire(db_session, 45)
    clear_settings_cache()
    assert get_setting(db_session, PICKUP_WINDOW).auto_expire_minutes == 45


def test_get_setting_missing_or_invalid_falls_back(
    db_session: Session, caplog: pytest.LogCaptureFixture
) -> None:
    db_session.execute(text("delete from public.system_settings where key = 'homework.defaults'"))
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        defaults = get_setting(db_session, HOMEWORK_DEFAULTS)
    assert isinstance(defaults, HomeworkDefaults)
    assert defaults == HOMEWORK_DEFAULTS.definition().default
    assert defaults.no_eta_reply_text == "已通知老師，稍後回覆預計時間"
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("homework.defaults" in r.getMessage() for r in warnings)

    caplog.clear()
    _set_value(db_session, "org.service_hours", {"mon": "bad"})
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        hours = get_setting(db_session, ORG_SERVICE_HOURS)
    assert hours == ORG_SERVICE_HOURS.definition().default
    assert hours.mon.start == "12:00"
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert any("org.service_hours" in r.getMessage() for r in errors)
    # 壞資料的內容不進 log
    assert all("bad" not in r.getMessage() for r in caplog.records)


def test_get_setting_secret_decrypted(
    db_session: Session, caplog: pytest.LogCaptureFixture
) -> None:
    _set_value(
        db_session,
        "line.messaging",
        {"channel_access_token": encrypt_token(_TOKEN), "channel_secret": None},
    )
    messaging = get_setting(db_session, LINE_MESSAGING)
    assert messaging.channel_access_token == _TOKEN
    assert messaging.channel_secret is None

    _set_value(
        db_session,
        "line.messaging",
        {"channel_access_token": "v1:garbage", "channel_secret": encrypt_token(_CHANNEL_SECRET)},
    )
    invalidate_setting("line.messaging")
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        messaging = get_setting(db_session, LINE_MESSAGING)
    assert messaging.channel_access_token is None
    # 解密失敗只影響該欄位
    assert messaging.channel_secret == _CHANNEL_SECRET
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert any(
        "line.messaging" in r.getMessage() and "channel_access_token" in r.getMessage()
        for r in errors
    )
    # 密文與明文都不進 log
    assert all("garbage" not in r.getMessage() for r in caplog.records)
    assert all(_CHANNEL_SECRET not in r.getMessage() for r in caplog.records)


def test_get_setting_invalidate_during_load_not_cached(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 查詢期間被 invalidate（後台剛改完）時，查到的舊值不可回寫快取
    original_load = settings_service._load

    def load_then_invalidated(session: Session, key: Any) -> Any:
        value = original_load(session, key)
        invalidate_setting(key.key)
        return value

    monkeypatch.setattr(settings_service, "_load", load_then_invalidated)
    assert get_setting(db_session, PICKUP_WINDOW).auto_expire_minutes == 120
    monkeypatch.setattr(settings_service, "_load", original_load)

    _set_auto_expire(db_session, 90)
    assert get_setting(db_session, PICKUP_WINDOW).auto_expire_minutes == 90
