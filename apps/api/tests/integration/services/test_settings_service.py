"""BACKEND-108：app/services/settings_service.py（get_setting、invalidate_setting、clear_settings_cache）。
BACKEND-110：put_setting（驗證、secret 加密與遮罩保留、稽核、commit 後失效 cache）。

DB 的變更都在 db_session 的 transaction 內，測試結束 rollback，不影響 seed；唯一的 committing 測試
以 owner 連線還原 pickup.window。
"""

import json
import logging
import time
from collections.abc import Iterator
from typing import Any
from uuid import UUID

import pytest
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.api.deps import CurrentStaff
from app.core.config import get_settings
from app.core.crypto import decrypt_token, derive_key, encrypt_token
from app.core.errors import AppError
from app.core.request_meta import RequestMeta
from app.core.settings_registry import (
    HOMEWORK_DEFAULTS,
    LINE_MESSAGING,
    NOTIFICATION_TOGGLES,
    ORG_SERVICE_HOURS,
    PICKUP_WINDOW,
    REGISTRY,
    HomeworkDefaults,
    PickupWindow,
    ServiceHours,
)
from app.core.tx_hooks import install_tx_hooks
from app.models.account import StaffUser
from app.models.audit import AuditLog
from app.schemas.settings import SettingOut
from app.services import settings_service
from app.services.settings_service import (
    SETTINGS_CACHE_TTL_SECONDS,
    clear_settings_cache,
    get_setting,
    invalidate_setting,
    list_settings_for_admin,
    mask_secret,
    put_setting,
)
from tests.integration.db.conftest import connect_owner
from tests.support.factories import make_staff

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


def test_get_setting_mutation_does_not_pollute_cache(db_session: Session) -> None:
    first = get_setting(db_session, NOTIFICATION_TOGGLES)
    assert first.root["pickup.requested"] is True

    # RootModel 的 frozen 擋不住內層 dict 的就地修改；回傳值必須是呼叫端自己的副本
    first.root["pickup.requested"] = False

    # 第二次命中快取：命中路徑回傳的也必須是副本
    second = get_setting(db_session, NOTIFICATION_TOGGLES)
    assert second.root["pickup.requested"] is True
    assert set(second.root.values()) == {True}
    second.root["exam.published"] = False

    third = get_setting(db_session, NOTIFICATION_TOGGLES)
    assert third.root["exam.published"] is True
    assert set(third.root.values()) == {True}


def test_get_setting_mutation_does_not_pollute_registry_default(db_session: Session) -> None:
    db_session.execute(
        text("delete from public.system_settings where key = 'notification.toggles'")
    )
    default = REGISTRY["notification.toggles"].default

    toggles = get_setting(db_session, NOTIFICATION_TOGGLES)
    assert toggles == default
    toggles.root["homework.done"] = False

    assert default.root["homework.done"] is True
    invalidate_setting("notification.toggles")
    again = get_setting(db_session, NOTIFICATION_TOGGLES)
    assert set(again.root.values()) == {True}
    assert again.root["homework.done"] is True


def test_list_settings_masked(db_session: Session) -> None:
    token = encrypt_token("abcdefghijkl1234")
    _set_value(
        db_session,
        "line.messaging",
        {"channel_access_token": token, "channel_secret": encrypt_token("short")},
    )

    items = {s.key: s for s in list_settings_for_admin(db_session)}

    messaging = items["line.messaging"]
    assert messaging.is_secret is True
    assert messaging.value == {"channel_access_token": "****1234", "channel_secret": "********"}
    dumped = json.dumps([s.model_dump(mode="json") for s in items.values()], ensure_ascii=False)
    assert "abcdefghijkl1234" not in dumped
    assert token not in dumped


def test_list_settings_unset_secret_stays_none(db_session: Session) -> None:
    _set_value(db_session, "line.messaging", {"channel_access_token": None, "channel_secret": None})

    items = {s.key: s for s in list_settings_for_admin(db_session)}

    assert items["line.messaging"].value == {"channel_access_token": None, "channel_secret": None}


def test_list_settings_order_and_schema(db_session: Session) -> None:
    items = list_settings_for_admin(db_session)

    assert [s.key for s in items] == list(REGISTRY)
    org = next(s for s in items if s.key == "org.profile")
    assert org.group == "org"
    assert org.label == "安親班資料"
    assert org.is_secret is False
    assert {"name", "logo_url"} <= set(org.json_schema["properties"])


def test_list_settings_reads_latest_without_cache(db_session: Session) -> None:
    before = next(s for s in list_settings_for_admin(db_session) if s.key == "pickup.window").value
    _set_auto_expire(db_session, 77)

    after = next(s for s in list_settings_for_admin(db_session) if s.key == "pickup.window").value

    assert after["auto_expire_minutes"] == 77
    assert before["auto_expire_minutes"] != 77


def test_list_settings_missing_row_uses_default(db_session: Session) -> None:
    db_session.execute(text("delete from public.system_settings where key = 'pickup.window'"))

    item = next(s for s in list_settings_for_admin(db_session) if s.key == "pickup.window")

    assert item.value == PICKUP_WINDOW.definition().default.model_dump(mode="json")
    assert item.updated_at is None
    assert item.updated_by_name is None


def test_list_settings_updated_by_name(db_session: Session) -> None:
    staff = make_staff(db_session, display_name="陳主任")
    db_session.execute(
        text("update public.system_settings set updated_by = :u where key = 'pickup.window'"),
        {"u": staff.id},
    )

    item = next(s for s in list_settings_for_admin(db_session) if s.key == "pickup.window")

    assert item.updated_by_name == "陳主任"
    assert item.updated_at is not None


def test_mask_secret() -> None:
    assert mask_secret(None) is None
    assert mask_secret("short") == "********"
    assert mask_secret("12345678") == "********"
    assert mask_secret("123456789") == "****6789"
    assert mask_secret("abcdefghijkl1234") == "****1234"


# --- BACKEND-110：put_setting ------------------------------------------------------------------

_META = RequestMeta(ip="203.0.113.5", user_agent="pytest", request_id=None)
_NEW_TOKEN = "tok-abcdefgh9999"  # noqa: S105  測試假值
_NEW_SECRET = "sec-12345678"  # noqa: S105  測試假值


def _current(staff: StaffUser) -> CurrentStaff:
    return CurrentStaff(
        id=staff.id,
        username=staff.username,
        display_name=staff.display_name,
        role_id=staff.role.id,
        role_code=staff.role.code,
        role_name=staff.role.name,
        permissions=frozenset(staff.role.permissions),
        must_change_password=staff.must_change_password,
        token_version=staff.token_version,
    )


@pytest.fixture
def actor(db_session: Session) -> CurrentStaff:
    return _current(make_staff(db_session, permissions=["settings:write"], display_name="陳主任"))


def _put(session: Session, actor: CurrentStaff, key: str, value: dict[str, Any]) -> SettingOut:
    return put_setting(session, key, value, actor=actor, meta=_META)


def _raw_value(session: Session, key: str) -> dict[str, Any]:
    row = session.execute(
        text("select value, updated_by from public.system_settings where key = :k"), {"k": key}
    ).one()
    return dict(row.value)


def _audit_rows(session: Session, key: str) -> list[AuditLog]:
    return list(
        session.execute(
            select(AuditLog)
            .where(AuditLog.action == "settings.update", AuditLog.entity_id == key)
            .order_by(AuditLog.created_at)
        ).scalars()
    )


def test_put_setting_success(db_session: Session, actor: CurrentStaff) -> None:
    value = {"name": "快樂安親班", "address": "臺北市", "phone": "02-0000-0001", "logo_url": None}

    out = _put(db_session, actor, "org.profile", value)

    assert out.key == "org.profile"
    assert out.value["name"] == "快樂安親班"
    assert out.value == value
    assert out.is_secret is False
    assert out.updated_by_name == "陳主任"
    assert out.updated_at is not None
    row = db_session.execute(
        text("select value, updated_by, is_secret from public.system_settings where key = :k"),
        {"k": "org.profile"},
    ).one()
    assert row.value == value
    assert row.updated_by == actor.id
    assert row.is_secret is False
    logs = _audit_rows(db_session, "org.profile")
    assert len(logs) == 1
    assert logs[0].entity_type == "system_setting"
    assert logs[0].actor_type == "staff"
    assert logs[0].actor_id == actor.id
    assert logs[0].after is not None
    assert logs[0].after["name"] == "快樂安親班"
    assert logs[0].before is not None
    assert logs[0].before["name"] == ""
    assert logs[0].ip == "203.0.113.5"


def test_put_setting_missing_row_is_inserted(db_session: Session, actor: CurrentStaff) -> None:
    db_session.execute(text("delete from public.system_settings where key = 'homework.window'"))

    out = _put(db_session, actor, "homework.window", {"past_days": 10, "future_days": 3})

    assert out.value == {"past_days": 10, "future_days": 3}
    assert _raw_value(db_session, "homework.window") == {"past_days": 10, "future_days": 3}


def test_put_setting_secret_encrypted(db_session: Session, actor: CurrentStaff) -> None:
    out = _put(
        db_session,
        actor,
        "line.messaging",
        {"channel_access_token": _NEW_TOKEN, "channel_secret": _NEW_SECRET},
    )

    stored = _raw_value(db_session, "line.messaging")
    assert stored["channel_access_token"].startswith("v1:")
    assert stored["channel_secret"].startswith("v1:")
    assert _NEW_TOKEN not in json.dumps(stored)
    assert _NEW_SECRET not in json.dumps(stored)
    assert decrypt_token(stored["channel_access_token"]) == _NEW_TOKEN
    assert decrypt_token(stored["channel_secret"]) == _NEW_SECRET
    assert out.value == {"channel_access_token": "****9999", "channel_secret": "****5678"}
    assert out.is_secret is True
    row = db_session.execute(
        text("select is_secret from public.system_settings where key = 'line.messaging'")
    ).one()
    assert row.is_secret is True
    # audit 不含明文與密文
    logs = _audit_rows(db_session, "line.messaging")
    assert len(logs) == 1
    dumped = json.dumps([logs[0].before, logs[0].after])
    assert _NEW_TOKEN not in dumped
    assert _NEW_SECRET not in dumped
    assert stored["channel_access_token"] not in dumped


def test_put_setting_secret_keep_masked(db_session: Session, actor: CurrentStaff) -> None:
    _put(
        db_session,
        actor,
        "line.messaging",
        {"channel_access_token": _NEW_TOKEN, "channel_secret": _NEW_SECRET},
    )
    first = _raw_value(db_session, "line.messaging")

    out = _put(
        db_session,
        actor,
        "line.messaging",
        {"channel_access_token": "****9999", "channel_secret": None},
    )

    stored = _raw_value(db_session, "line.messaging")
    assert stored["channel_access_token"] == first["channel_access_token"]
    assert decrypt_token(stored["channel_access_token"]) == _NEW_TOKEN
    assert stored["channel_secret"] is None
    assert out.value == {"channel_access_token": "****9999", "channel_secret": None}

    # 全遮罩值（短 secret）同樣代表不修改；遮罩值不會被當成新明文存入
    _put(
        db_session,
        actor,
        "line.messaging",
        {"channel_access_token": "****9999", "channel_secret": "short1"},
    )
    _put(
        db_session,
        actor,
        "line.messaging",
        {"channel_access_token": "********", "channel_secret": "********"},
    )
    stored = _raw_value(db_session, "line.messaging")
    assert decrypt_token(stored["channel_access_token"]) == _NEW_TOKEN
    assert decrypt_token(stored["channel_secret"]) == "short1"


def test_put_setting_unknown_key(db_session: Session, actor: CurrentStaff) -> None:
    with pytest.raises(AppError) as excinfo:
        _put(db_session, actor, "foo.bar", {})
    assert excinfo.value.status == 404
    assert excinfo.value.code == "setting_not_found"
    assert _audit_rows(db_session, "foo.bar") == []


def test_put_setting_invalid(db_session: Session, actor: CurrentStaff) -> None:
    value = {
        "request_start": "12:00",
        "request_end": "19:00",
        "latest_expected_arrival": "19:00",
        "auto_expire_minutes": 5,
    }

    with pytest.raises(AppError) as excinfo:
        _put(db_session, actor, "pickup.window", value)

    assert excinfo.value.status == 422
    assert excinfo.value.code == "invalid_setting_value"
    details = excinfo.value.details
    assert isinstance(details, list)
    assert "auto_expire_minutes" in details[0]["loc"]
    assert set(details[0]) == {"loc", "msg", "type"}
    assert _raw_value(db_session, "pickup.window")["auto_expire_minutes"] == 120
    assert _audit_rows(db_session, "pickup.window") == []

    # 多餘欄位（extra='forbid'）也是 422
    with pytest.raises(AppError) as extra:
        _put(db_session, actor, "pickup.window", {**value, "auto_expire_minutes": 60, "x": 1})
    assert extra.value.code == "invalid_setting_value"
    # 非 dict 的 secret 值（例如數字）不會被誤當遮罩，由 schema 擋下
    with pytest.raises(AppError) as bad_secret:
        _put(db_session, actor, "line.messaging", {"channel_access_token": 123})
    assert bad_secret.value.code == "invalid_setting_value"


def test_put_setting_cache_not_polluted_before_commit(
    db_session: Session, actor: CurrentStaff
) -> None:
    """交易內 put 後、commit 前，快取仍是舊值（invalidate 掛在 after_commit）。"""
    assert get_setting(db_session, PICKUP_WINDOW).auto_expire_minutes == 120
    value = {
        "request_start": "12:00",
        "request_end": "19:00",
        "latest_expected_arrival": "19:00",
        "auto_expire_minutes": 90,
    }
    _put(db_session, actor, "pickup.window", value)
    assert get_setting(db_session, PICKUP_WINDOW).auto_expire_minutes == 120

    db_session.rollback()
    assert get_setting(db_session, PICKUP_WINDOW).auto_expire_minutes == 120


@pytest.fixture
def owner_restore_pickup_window() -> Iterator[list[tuple[UUID, UUID]]]:
    """測試結束以 owner 連線把 pickup.window 還原為 seed 值，並刪掉測試建立的 (staff, role)。

    排在 committing_db_session 之前（先 close session 再刪列）。
    """
    ids: list[tuple[UUID, UUID]] = []
    yield ids
    default = PICKUP_WINDOW.definition().default.model_dump(mode="json")
    with connect_owner() as conn:
        conn.execute("set lock_timeout = '5s'")
        conn.execute(
            "update public.system_settings set value = %s::jsonb, updated_by = null "
            "where key = 'pickup.window'",
            (json.dumps(default),),
        )
        for staff_id, role_id in ids:
            conn.execute("delete from public.staff_users where id = %s", (staff_id,))
            conn.execute("delete from public.roles where id = %s", (role_id,))
        conn.commit()
    clear_settings_cache()


@pytest.mark.cleanup_tables("audit_logs")
def test_put_setting_invalidate_after_commit(
    owner_restore_pickup_window: list[tuple[UUID, UUID]], committing_db_session: Session
) -> None:
    install_tx_hooks()
    session = committing_db_session
    staff = make_staff(session, permissions=["settings:write"])
    session.commit()
    owner_restore_pickup_window.append((staff.id, staff.role.id))
    actor = _current(staff)
    assert get_setting(session, PICKUP_WINDOW).auto_expire_minutes == 120
    value = {
        "request_start": "12:00",
        "request_end": "19:00",
        "latest_expected_arrival": "19:00",
        "auto_expire_minutes": 90,
    }

    _put(session, actor, "pickup.window", value)
    # commit 前快取仍是舊值
    assert get_setting(session, PICKUP_WINDOW).auto_expire_minutes == 120
    session.commit()

    # commit 後立即反映（不需等 TTL）
    assert get_setting(session, PICKUP_WINDOW).auto_expire_minutes == 90
