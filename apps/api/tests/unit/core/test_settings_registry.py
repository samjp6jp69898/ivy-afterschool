"""BACKEND-106：system_settings registry。

11 個 key 的 schema、預設值、secret、公開欄位、中文標籤與分組。
"""

import re
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from app.core.settings_registry import (
    GROUP_LABELS,
    HOMEWORK_DEFAULTS,
    HOMEWORK_WINDOW,
    LEAVE_WINDOW,
    LINE_LIFF,
    LINE_MESSAGING,
    NOTIFICATION_TOGGLES,
    ORG_PROFILE,
    ORG_SERVICE_HOURS,
    PICKUP_AUTHORIZATION,
    PICKUP_PERSONS,
    PICKUP_WINDOW,
    REGISTRY,
    DayHours,
    HomeworkDefaults,
    HomeworkWindow,
    LeaveWindow,
    LineLiff,
    LineMessaging,
    NotificationToggles,
    OrgProfile,
    PickupAuthorizationSettings,
    PickupPersonsSettings,
    PickupWindow,
    ServiceHours,
    SettingDef,
    SettingKey,
)
from app.notifications.events import EVENTS, Event

_CJK = re.compile(r"[一-鿿]")

_WEEKDAY_OPEN = {"open": True, "start": "12:00", "end": "19:00"}

# DB-038 data migration 的 value，逐欄比對（registry 與 seed 是同一份介面）
_DB038_SEED: dict[str, dict[str, Any]] = {
    "org.profile": {"name": "", "address": "", "phone": "", "logo_url": None},
    "org.service_hours": {
        "mon": _WEEKDAY_OPEN,
        "tue": _WEEKDAY_OPEN,
        "wed": _WEEKDAY_OPEN,
        "thu": _WEEKDAY_OPEN,
        "fri": _WEEKDAY_OPEN,
        "sat": {"open": False, "start": "08:00", "end": "12:00"},
    },
    "pickup.window": {
        "request_start": "12:00",
        "request_end": "19:00",
        "latest_expected_arrival": "19:00",
        "auto_expire_minutes": 120,
    },
    "homework.defaults": {
        "auto_reply_without_eta": True,
        "no_eta_reply_text": "已通知老師，稍後回覆預計時間",
    },
    "notification.toggles": {e.value: True for e in Event},
    "line.liff": {"liff_id": "", "channel_id": "", "add_friend_url": ""},
    "leave.window": {
        "past_days": 30,
        "future_days": 60,
        "max_attachments": 3,
        "max_attachment_mb": 10,
    },
    "pickup.authorization": {"max_days_ahead": 14, "max_active_per_day": 3},
    "pickup.persons": {"max_per_student": 10},
    "homework.window": {"past_days": 30, "future_days": 7},
    "line.messaging": {"channel_access_token": None, "channel_secret": None},
}

_PICKUP_WINDOW_OK = {
    "request_start": "12:00",
    "request_end": "19:00",
    "latest_expected_arrival": "19:00",
    "auto_expire_minutes": 120,
}
_LEAVE_WINDOW_OK = {
    "past_days": 30,
    "future_days": 60,
    "max_attachments": 3,
    "max_attachment_mb": 10,
}


def test_settings_registry_keys() -> None:
    assert set(REGISTRY) == {
        "org.profile",
        "org.service_hours",
        "pickup.window",
        "homework.defaults",
        "notification.toggles",
        "line.liff",
        "line.messaging",
        "leave.window",
        "pickup.authorization",
        "pickup.persons",
        "homework.window",
    }
    for key, d in REGISTRY.items():
        assert isinstance(d, SettingDef)
        assert d.key == key
        assert d.schema.model_validate(d.default.model_dump(by_alias=True)) == d.default
        assert isinstance(d.default, d.schema)
        assert _CJK.search(d.label), key


def test_settings_registry_defaults_match_db038_seed() -> None:
    assert set(_DB038_SEED) == set(REGISTRY)
    for key, d in REGISTRY.items():
        assert d.default.model_dump(mode="json", by_alias=True) == _DB038_SEED[key], key
        # seed 的 JSON 也必須能通過 schema（後端讀 DB 值時走這條路）
        assert d.schema.model_validate(_DB038_SEED[key]) == d.default, key


def test_settings_registry_service_hours_rules() -> None:
    with pytest.raises(ValidationError):
        DayHours(open=True, start="19:00", end="12:00")
    with pytest.raises(ValidationError):
        DayHours(open=True, start="12:00", end="12:00")
    with pytest.raises(ValidationError):
        DayHours(open=True, start="25:00", end="26:00")
    for bad in ("9:00", "09:60", "24:00", "0900", "09:00:00", " 09:00", "09:00\n"):
        with pytest.raises(ValidationError):
            DayHours(open=True, start=bad, end="23:00")
    # 不營業時不檢查先後
    assert DayHours(open=False, start="19:00", end="12:00").start == "19:00"
    assert DayHours(open=True, start="00:00", end="23:59").end == "23:59"

    # 週日不列：多 sun 被拒
    payload = dict(_DB038_SEED["org.service_hours"])
    with pytest.raises(ValidationError):
        ServiceHours.model_validate({**payload, "sun": _WEEKDAY_OPEN})
    payload.pop("sat")
    with pytest.raises(ValidationError):
        ServiceHours.model_validate(payload)


def test_settings_registry_pickup_window_rules() -> None:
    assert PickupWindow.model_validate(_PICKUP_WINDOW_OK).auto_expire_minutes == 120
    for minutes, ok in ((5, False), (9, False), (10, True), (600, True), (601, False)):
        payload = {**_PICKUP_WINDOW_OK, "auto_expire_minutes": minutes}
        if ok:
            PickupWindow.model_validate(payload)
        else:
            with pytest.raises(ValidationError):
                PickupWindow.model_validate(payload)

    with pytest.raises(ValidationError):
        PickupWindow.model_validate({**_PICKUP_WINDOW_OK, "latest_expected_arrival": "18:30"})
    PickupWindow.model_validate({**_PICKUP_WINDOW_OK, "latest_expected_arrival": "20:00"})
    with pytest.raises(ValidationError):
        PickupWindow.model_validate({**_PICKUP_WINDOW_OK, "request_start": "19:00"})
    with pytest.raises(ValidationError):
        PickupWindow.model_validate({**_PICKUP_WINDOW_OK, "request_start": "7:00"})


def test_settings_registry_homework_defaults_rules() -> None:
    with pytest.raises(ValidationError):
        HomeworkDefaults(auto_reply_without_eta=True, no_eta_reply_text="")
    with pytest.raises(ValidationError):
        HomeworkDefaults(auto_reply_without_eta=True, no_eta_reply_text="字" * 101)
    assert (
        len(
            HomeworkDefaults(
                auto_reply_without_eta=False, no_eta_reply_text="字" * 100
            ).no_eta_reply_text
        )
        == 100
    )


def test_settings_registry_toggles_exact_keys() -> None:
    full = {e.value: True for e in Event}
    assert NotificationToggles.model_validate(full).root == full

    missing = dict(full)
    missing.pop("exam.published")
    with pytest.raises(ValidationError):
        NotificationToggles.model_validate(missing)

    with pytest.raises(ValidationError):
        NotificationToggles.model_validate({**full, "foo.bar": True})

    mixed = {**full, "pickup.arrived": False}
    assert NotificationToggles.model_validate(mixed).root["pickup.arrived"] is False


def test_settings_registry_secret_and_public() -> None:
    assert [k for k, d in REGISTRY.items() if d.is_secret] == ["line.messaging"]
    assert REGISTRY["line.messaging"].secret_fields == ("channel_access_token", "channel_secret")
    assert REGISTRY["org.profile"].public_fields == ("name", "phone", "logo_url")
    assert REGISTRY["leave.window"].public_fields == (
        "past_days",
        "future_days",
        "max_attachments",
        "max_attachment_mb",
    )
    assert REGISTRY["pickup.authorization"].public_fields == ("max_days_ahead",)
    assert REGISTRY["line.messaging"].public_fields == ()
    for key in (
        "org.service_hours",
        "pickup.window",
        "homework.defaults",
        "notification.toggles",
        "homework.window",
    ):
        assert REGISTRY[key].public_fields == (), key
    for key, d in REGISTRY.items():
        if key != "line.messaging":
            assert d.secret_fields == (), key


def test_settings_registry_field_lists_are_consistent() -> None:
    for key, d in REGISTRY.items():
        fields = set(d.schema.model_fields) if key != "notification.toggles" else set()
        assert set(d.public_fields) <= fields, key
        assert set(d.secret_fields) <= fields, key
        assert not set(d.public_fields) & set(d.secret_fields), key
        assert d.is_secret == bool(d.secret_fields), key


def test_settings_registry_extra_forbid() -> None:
    with pytest.raises(ValidationError):
        OrgProfile.model_validate(
            {"name": "x", "address": "", "phone": "", "logo_url": None, "fax": "1"}
        )
    with pytest.raises(ValidationError):
        DayHours.model_validate({"open": True, "start": "12:00", "end": "19:00", "note": "x"})
    with pytest.raises(ValidationError):
        LineMessaging.model_validate({"channel_access_token": None, "channel_secret": None, "x": 1})
    # notification.toggles 以 RootModel 的 key 集合檢查代替 extra='forbid'
    for d in REGISTRY.values():
        if d.key != "notification.toggles":
            assert d.schema.model_config.get("extra") == "forbid", d.key


def test_settings_registry_org_profile_rules() -> None:
    ok = OrgProfile.model_validate(
        {
            "name": "快樂安親班",
            "address": "台北市",
            "phone": "02-0000-0000",
            "logo_url": "https://as.example.com/logo.png",
        }
    )
    assert str(ok.logo_url) == "https://as.example.com/logo.png"
    for field, value in (
        ("name", "名" * 51),
        ("address", "址" * 201),
        ("phone", "0" * 21),
        ("logo_url", "not-a-url"),
    ):
        payload = {"name": "", "address": "", "phone": "", "logo_url": None, field: value}
        with pytest.raises(ValidationError):
            OrgProfile.model_validate(payload)


def test_settings_registry_line_liff_channel_id() -> None:
    ok = LineLiff.model_validate({"liff_id": "1657000000-AbcdEfgh", "channel_id": "1657000000"})
    assert ok.add_friend_url == ""
    with pytest.raises(ValidationError):
        LineLiff.model_validate({"liff_id": "1657000000-AbcdEfgh", "channel_id": "abc"})
    with pytest.raises(ValidationError):
        LineLiff.model_validate({"liff_id": "1657000000-AbcdEfgh", "channel_id": "12345"})
    with pytest.raises(ValidationError):
        LineLiff.model_validate({"liff_id": "1657000000-AbcdEfgh"})
    with pytest.raises(ValidationError):
        LineLiff.model_validate({"liff_id": "not a liff", "channel_id": ""})
    assert REGISTRY["line.liff"].public_fields == ("liff_id", "add_friend_url")
    default = REGISTRY["line.liff"].default
    assert isinstance(default, LineLiff)
    assert default.channel_id == ""

    base = {"liff_id": "", "channel_id": ""}
    with pytest.raises(ValidationError):
        LineLiff.model_validate({**base, "add_friend_url": "http://x"})
    with pytest.raises(ValidationError):
        LineLiff.model_validate({**base, "add_friend_url": "https://" + "a" * 293})
    assert (
        LineLiff.model_validate({**base, "add_friend_url": "https://lin.ee/abc"}).add_friend_url
        == "https://lin.ee/abc"
    )


def test_settings_registry_line_messaging_rules() -> None:
    secret = "f" * 32  # 假值
    ok = LineMessaging.model_validate({"channel_access_token": "t" * 500, "channel_secret": secret})
    assert ok.channel_secret == secret
    with pytest.raises(ValidationError):
        LineMessaging.model_validate({"channel_access_token": "t" * 501, "channel_secret": None})


def test_settings_registry_defaults_match_seed() -> None:
    service_hours = REGISTRY["org.service_hours"].default
    assert isinstance(service_hours, ServiceHours)
    assert service_hours.mon == DayHours(open=True, start="12:00", end="19:00")
    assert service_hours.sat.open is False
    assert REGISTRY["pickup.window"].default.model_dump() == {
        "request_start": "12:00",
        "request_end": "19:00",
        "latest_expected_arrival": "19:00",
        "auto_expire_minutes": 120,
    }


def _walk_properties(schema: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    found: list[tuple[str, dict[str, Any]]] = []
    for name, prop in schema.get("properties", {}).items():
        found.append((name, prop))
    for def_name, sub in schema.get("$defs", {}).items():
        for name, prop in sub.get("properties", {}).items():
            found.append((f"{def_name}.{name}", prop))
    return found


def test_settings_registry_field_titles_chinese() -> None:
    for key, d in REGISTRY.items():
        schema = d.schema.model_json_schema()
        assert _CJK.search(schema["title"]), key
        for name, prop in _walk_properties(schema):
            assert _CJK.search(prop.get("title", "")), (key, name)
            assert prop.get("description", "").strip(), (key, name)
        for def_name, sub in schema.get("$defs", {}).items():
            assert _CJK.search(sub["title"]), (key, def_name)

    pickup = REGISTRY["pickup.window"].schema.model_json_schema()
    assert pickup["properties"]["auto_expire_minutes"]["title"] == "接送請求自動過期分鐘數"
    toggles = REGISTRY["notification.toggles"].schema.model_json_schema()
    assert toggles["title"] == "通知開關"
    assert toggles["x-labels"]["homework.done"] == EVENTS[Event.HOMEWORK_DONE].label
    assert toggles["x-labels"] == {e.value: EVENTS[e].label for e in Event}

    hours = REGISTRY["org.service_hours"].schema.model_json_schema()
    assert [
        hours["properties"][d]["title"] for d in ("mon", "tue", "wed", "thu", "fri", "sat")
    ] == [
        "週一",
        "週二",
        "週三",
        "週四",
        "週五",
        "週六",
    ]
    day = hours["$defs"]["DayHours"]["properties"]
    assert (day["open"]["title"], day["start"]["title"], day["end"]["title"]) == (
        "是否營業",
        "開始時間",
        "結束時間",
    )


def test_settings_registry_field_titles_exact() -> None:
    expected: dict[type[BaseModel], dict[str, str]] = {
        OrgProfile: {
            "name": "安親班名稱",
            "address": "地址",
            "phone": "聯絡電話",
            "logo_url": "Logo 圖片網址",
        },
        PickupWindow: {
            "request_start": "可發起接送開始時間",
            "request_end": "可發起接送結束時間",
            "latest_expected_arrival": "最晚預計抵達時間",
            "auto_expire_minutes": "接送請求自動過期分鐘數",
        },
        HomeworkDefaults: {
            "auto_reply_without_eta": "未設定預計時間時自動回覆",
            "no_eta_reply_text": "自動回覆文案",
        },
        LeaveWindow: {
            "past_days": "家長可申請的過去天數",
            "future_days": "家長可申請的未來天數",
            "max_attachments": "每筆請假附件數上限",
            "max_attachment_mb": "單一附件大小上限（MB）",
        },
        PickupAuthorizationSettings: {
            "max_days_ahead": "代理授權最多可提前天數",
            "max_active_per_day": "同一學生同日代理授權上限",
        },
        PickupPersonsSettings: {"max_per_student": "每位學生常用接送人上限"},
        HomeworkWindow: {
            "past_days": "作業可編輯的過去天數",
            "future_days": "作業可預先新增的天數",
        },
        LineLiff: {
            "liff_id": "LIFF 應用程式 ID",
            "channel_id": "LINE 登入 Channel ID",
            "add_friend_url": "官方帳號加好友網址",
        },
        LineMessaging: {
            "channel_access_token": "LINE 訊息 Channel Access Token",
            "channel_secret": "LINE 訊息 Channel Secret",
        },
    }
    for model, titles in expected.items():
        assert {name: f.title for name, f in model.model_fields.items()} == titles, model.__name__
    desc = PickupWindow.model_fields["auto_expire_minutes"].description
    assert desc == "家長發起後超過此分鐘數仍未完成即自動過期"


def test_settings_registry_limits_defaults() -> None:
    assert REGISTRY["leave.window"].default.model_dump() == {
        "past_days": 30,
        "future_days": 60,
        "max_attachments": 3,
        "max_attachment_mb": 10,
    }
    with pytest.raises(ValidationError):
        PickupAuthorizationSettings(max_days_ahead=14, max_active_per_day=0)
    with pytest.raises(ValidationError):
        LeaveWindow.model_validate({**_LEAVE_WINDOW_OK, "max_attachment_mb": 11})
    homework_window = REGISTRY["homework.window"].default
    assert isinstance(homework_window, HomeworkWindow)
    assert homework_window.future_days == 7
    assert REGISTRY["pickup.persons"].public_fields == ("max_per_student",)


@pytest.mark.parametrize(
    ("model", "field", "lowest", "highest"),
    [
        (LeaveWindow, "past_days", 0, 365),
        (LeaveWindow, "future_days", 1, 365),
        (LeaveWindow, "max_attachments", 0, 10),
        (LeaveWindow, "max_attachment_mb", 1, 10),
        (PickupAuthorizationSettings, "max_days_ahead", 0, 60),
        (PickupAuthorizationSettings, "max_active_per_day", 1, 10),
        (PickupPersonsSettings, "max_per_student", 1, 50),
        (HomeworkWindow, "past_days", 0, 365),
        (HomeworkWindow, "future_days", 0, 60),
    ],
)
def test_settings_registry_range_boundaries(
    model: type[BaseModel], field: str, lowest: int, highest: int
) -> None:
    default = next(d.default for d in REGISTRY.values() if d.schema is model).model_dump()
    model.model_validate({**default, field: lowest})
    model.model_validate({**default, field: highest})
    with pytest.raises(ValidationError):
        model.model_validate({**default, field: lowest - 1})
    with pytest.raises(ValidationError):
        model.model_validate({**default, field: highest + 1})


def test_settings_registry_groups() -> None:
    groups = {k: d.group for k, d in REGISTRY.items()}
    assert groups == {
        "org.profile": "org",
        "org.service_hours": "org",
        "pickup.window": "pickup",
        "pickup.authorization": "pickup",
        "pickup.persons": "pickup",
        "homework.defaults": "homework",
        "homework.window": "homework",
        "leave.window": "leave",
        "notification.toggles": "notification",
        "line.liff": "line",
        "line.messaging": "line",
    }
    assert dict(GROUP_LABELS) == {
        "org": "安親班資訊",
        "pickup": "接送",
        "homework": "作業",
        "leave": "請假",
        "notification": "通知",
        "line": "LINE",
    }


def test_settings_registry_typed_handles() -> None:
    handles: list[SettingKey[Any]] = [
        ORG_PROFILE,
        ORG_SERVICE_HOURS,
        PICKUP_WINDOW,
        HOMEWORK_DEFAULTS,
        NOTIFICATION_TOGGLES,
        LINE_LIFF,
        LINE_MESSAGING,
        LEAVE_WINDOW,
        PICKUP_AUTHORIZATION,
        PICKUP_PERSONS,
        HOMEWORK_WINDOW,
    ]
    assert {h.key for h in handles} == set(REGISTRY)
    for handle in handles:
        assert handle.definition() is REGISTRY[handle.key]
        assert handle.schema is REGISTRY[handle.key].schema
    # 型別化：definition().default 的型別即 schema（mypy 會檢查 .name 存在）
    assert ORG_PROFILE.definition().default.name == ""
    assert PICKUP_WINDOW.definition().default.auto_expire_minutes == 120


def test_settings_registry_defaults_are_immutable() -> None:
    default = REGISTRY["pickup.window"].default
    with pytest.raises(ValidationError):
        default.auto_expire_minutes = 1  # type: ignore[misc]
