"""DB-038：system_settings 預設值 seed（db038 的 SEED_SQL）。

只讀 seed 列；冪等測試以 owner（migration 的身分）在 transaction 內重跑 SEED_SQL，結束 rollback。
"""

import json
from typing import Any

from tests.integration.db.conftest import Conn, load_seed_sql

KEYS = [
    "homework.defaults",
    "homework.window",
    "leave.window",
    "line.liff",
    "line.messaging",
    "notification.toggles",
    "org.profile",
    "org.service_hours",
    "pickup.authorization",
    "pickup.persons",
    "pickup.window",
]

# domain_spec M9 的 13 個事件（與 BACKEND-201 Event、db029 ck_notifications_event 一致）
EVENTS = {
    "attendance.checked_in",
    "attendance.checked_out",
    "leave.created",
    "leave.cancelled",
    "homework.eta_updated",
    "homework.done",
    "pickup.requested",
    "pickup.replied",
    "pickup.arrived",
    "pickup.completed",
    "pickup.cancelled",
    "exam.published",
    "binding.completed",
}

WEEKDAY_HOURS = {"open": True, "start": "12:00", "end": "19:00"}


def _value(conn: Conn, key: str) -> Any:
    row = conn.execute("select value from public.system_settings where key = %s", (key,)).fetchone()
    assert row is not None, f"seed 缺少 key={key}"
    return row[0]


def test_seed_system_settings_keys(backend_conn: Conn) -> None:
    rows = backend_conn.execute("select key from public.system_settings order by key").fetchall()

    assert [key for (key,) in rows] == KEYS


def test_seed_system_settings_org_values(backend_conn: Conn) -> None:
    assert _value(backend_conn, "org.profile") == {
        "name": "",
        "address": "",
        "phone": "",
        "logo_url": None,
    }
    hours = _value(backend_conn, "org.service_hours")
    assert set(hours) == {"mon", "tue", "wed", "thu", "fri", "sat"}
    for day in ("mon", "tue", "wed", "thu", "fri"):
        assert hours[day] == WEEKDAY_HOURS
    assert hours["sat"] == {"open": False, "start": "08:00", "end": "12:00"}


def test_seed_system_settings_values(backend_conn: Conn) -> None:
    assert _value(backend_conn, "pickup.window") == {
        "request_start": "12:00",
        "request_end": "19:00",
        "latest_expected_arrival": "19:00",
        "auto_expire_minutes": 120,
    }
    assert _value(backend_conn, "homework.defaults") == {
        "auto_reply_without_eta": True,
        "no_eta_reply_text": "已通知老師，稍後回覆預計時間",
    }
    assert _value(backend_conn, "line.liff") == {
        "liff_id": "",
        "channel_id": "",
        "add_friend_url": "",
    }


def test_seed_system_settings_limit_values(backend_conn: Conn) -> None:
    assert _value(backend_conn, "leave.window") == {
        "past_days": 30,
        "future_days": 60,
        "max_attachments": 3,
        "max_attachment_mb": 10,
    }
    assert _value(backend_conn, "pickup.authorization") == {
        "max_days_ahead": 14,
        "max_active_per_day": 3,
    }
    assert _value(backend_conn, "pickup.persons") == {"max_per_student": 10}
    assert _value(backend_conn, "homework.window") == {"past_days": 30, "future_days": 7}


def test_seed_system_settings_secret_flag(backend_conn: Conn) -> None:
    secret_keys = backend_conn.execute(
        "select key from public.system_settings where is_secret"
    ).fetchall()
    assert secret_keys == [("line.messaging",)]

    assert _value(backend_conn, "line.messaging") == {
        "channel_access_token": None,
        "channel_secret": None,
    }


def test_seed_system_settings_notification_toggles_complete(backend_conn: Conn) -> None:
    toggles = _value(backend_conn, "notification.toggles")

    assert set(toggles) == EVENTS
    assert all(enabled is True for enabled in toggles.values())


def test_seed_system_settings_idempotent(owner_conn: Conn) -> None:
    changed = json.dumps(
        {"liff_id": "1234567890-abcdefgh", "channel_id": "1234567890", "add_friend_url": ""}
    )
    owner_conn.execute(
        "update public.system_settings set value = %s::jsonb where key = 'line.liff'", (changed,)
    )

    owner_conn.execute(load_seed_sql("db038_seed_system_settings.py"))

    liff = _value(owner_conn, "line.liff")
    assert liff["liff_id"] == "1234567890-abcdefgh"
    assert liff["channel_id"] == "1234567890"
    count = owner_conn.execute("select count(*) from public.system_settings").fetchone()
    assert count == (len(KEYS),)
