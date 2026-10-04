"""BACKEND-107：DB-038 system_settings seed 與 app/core/settings_registry.py 的一致性。

直接查表（不經 SettingsService 的 cache），以 app_backend 的 db_session 讀取。值的比對以乾淨
``just db-reset`` 後的 DB 為準：本機後台改過設定時先 reset 再跑。
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.settings_registry import REGISTRY
from app.models.reference import SystemSetting

_WEEKDAY = {"open": True, "start": "12:00", "end": "19:00"}


def _rows(db_session: Session) -> dict[str, SystemSetting]:
    return {row.key: row for row in db_session.execute(select(SystemSetting)).scalars()}


def _default_json(key: str) -> Any:
    return REGISTRY[key].default.model_dump(mode="json", by_alias=True)


def test_settings_seed_all_keys_present(db_session: Session) -> None:
    keys = set(_rows(db_session))

    # 雙向列出：DB 缺的 key、registry 未註冊的 key
    assert (sorted(set(REGISTRY) - keys), sorted(keys - set(REGISTRY))) == ([], [])
    assert len(keys) == 11


def test_settings_seed_values_match_defaults(db_session: Session) -> None:
    rows = _rows(db_session)

    invalid = []
    for key, definition in REGISTRY.items():
        try:
            definition.schema.model_validate(rows[key].value)
        except ValueError:
            invalid.append(key)
    assert invalid == []

    mismatched = {
        key: {"db": rows[key].value, "registry": _default_json(key)}
        for key in REGISTRY
        if rows[key].value != _default_json(key)
    }
    assert mismatched == {}


def test_settings_seed_secret_flags(db_session: Session) -> None:
    rows = _rows(db_session)

    secret_in_db = {key for key, row in rows.items() if row.is_secret}
    assert secret_in_db == {"line.messaging"}
    assert secret_in_db == {key for key, d in REGISTRY.items() if d.is_secret}


def test_settings_seed_service_hours_values(db_session: Session) -> None:
    value = _rows(db_session)["org.service_hours"].value

    assert value == {
        "mon": _WEEKDAY,
        "tue": _WEEKDAY,
        "wed": _WEEKDAY,
        "thu": _WEEKDAY,
        "fri": _WEEKDAY,
        "sat": {"open": False, "start": "08:00", "end": "12:00"},
    }
    assert "sun" not in value
    assert value == _default_json("org.service_hours")


def test_settings_seed_pickup_window_values(db_session: Session) -> None:
    value = _rows(db_session)["pickup.window"].value

    assert value == {
        "request_start": "12:00",
        "request_end": "19:00",
        "latest_expected_arrival": "19:00",
        "auto_expire_minutes": 120,
    }
    assert value == _default_json("pickup.window")
