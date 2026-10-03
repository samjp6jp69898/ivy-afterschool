"""BACKEND-106：system_settings 的 key 註冊表（architecture_decisions §4、domain_spec M2）。

- 每個 key：Pydantic schema（extra='forbid'）、預設值、是否 secret、加密欄位、公開欄位、
  中文標籤與後台分組。預設值與 DB-038 data migration 一致（有測試逐欄比對）。
- 每個欄位都以 ``Field(title, description)`` 宣告，GET /api/admin/settings 回傳的
  json_schema 可直接當表單標籤（BACKEND-109）。
- 時間一律 ``HH:MM`` 字串（Asia/Taipei 當地時間）。
- 固定業務規則不進 registry（domain_spec M2）：員工密碼規則、預計抵達時間可早於現在 5 分鐘、
  綁定碼效期 7 天、接送碼連錯 5 次鎖定。
- 新增設定項：先在此註冊，再以 migration seed 預設值；不得為業務參數新增 env。
- 本模組可 import ``app.notifications.events``，反向不可（避免循環）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Annotated, Any, Final, Literal, Self, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    RootModel,
    StringConstraints,
    model_validator,
)

from app.notifications.events import EVENTS, Event

SettingGroup = Literal["org", "pickup", "homework", "leave", "notification", "line"]

GROUP_LABELS: Final[Mapping[SettingGroup, str]] = MappingProxyType(
    {
        "org": "安親班資訊",
        "pickup": "接送",
        "homework": "作業",
        "leave": "請假",
        "notification": "通知",
        "line": "LINE",
    }
)

TimeHM = Annotated[str, StringConstraints(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")]


class _SettingModel(BaseModel):
    # frozen：預設值是模組層級共用的實例，不可被就地修改
    model_config = ConfigDict(extra="forbid", frozen=True)


# --- org ---------------------------------------------------------------------------------


class OrgProfile(_SettingModel):
    model_config = ConfigDict(title="安親班資料")

    name: str = Field(max_length=50, title="安親班名稱", description="顯示於後台與家長端的名稱")
    address: str = Field(max_length=200, title="地址", description="安親班地址")
    phone: str = Field(max_length=20, title="聯絡電話", description="家長端顯示的聯絡電話")
    logo_url: HttpUrl | None = Field(
        title="Logo 圖片網址", description="顯示於家長端與後台的 Logo 圖片網址，可留空"
    )


class DayHours(_SettingModel):
    model_config = ConfigDict(title="單日營業時段")

    open: bool = Field(title="是否營業", description="當天是否營業")
    start: TimeHM = Field(title="開始時間", description="營業開始時間（HH:MM）")
    end: TimeHM = Field(title="結束時間", description="營業結束時間（HH:MM），須晚於開始時間")

    @model_validator(mode="after")
    def _start_before_end(self) -> Self:
        if self.open and self.start >= self.end:
            raise ValueError("營業時，開始時間必須早於結束時間")
        return self


class ServiceHours(_SettingModel):
    """週日一律不營業，不列。"""

    model_config = ConfigDict(title="營業時段")

    mon: DayHours = Field(title="週一", description="週一的營業時段")
    tue: DayHours = Field(title="週二", description="週二的營業時段")
    wed: DayHours = Field(title="週三", description="週三的營業時段")
    thu: DayHours = Field(title="週四", description="週四的營業時段")
    fri: DayHours = Field(title="週五", description="週五的營業時段")
    sat: DayHours = Field(title="週六", description="週六的營業時段")


# --- pickup ------------------------------------------------------------------------------


class PickupWindow(_SettingModel):
    model_config = ConfigDict(title="接送時段")

    request_start: TimeHM = Field(
        title="可發起接送開始時間", description="家長每天最早可發起「我要來接」的時間"
    )
    request_end: TimeHM = Field(
        title="可發起接送結束時間", description="家長每天最晚可發起「我要來接」的時間"
    )
    latest_expected_arrival: TimeHM = Field(
        title="最晚預計抵達時間", description="家長可選的最晚預計抵達時間，不可早於結束時間"
    )
    auto_expire_minutes: int = Field(
        ge=10,
        le=600,
        title="接送請求自動過期分鐘數",
        description="家長發起後超過此分鐘數仍未完成即自動過期",
    )

    @model_validator(mode="after")
    def _check_order(self) -> Self:
        if self.request_start >= self.request_end:
            raise ValueError("可發起接送開始時間必須早於結束時間")
        if self.latest_expected_arrival < self.request_end:
            raise ValueError("最晚預計抵達時間不可早於可發起接送結束時間")
        return self


class PickupAuthorizationSettings(_SettingModel):
    model_config = ConfigDict(title="代理接送授權")

    max_days_ahead: int = Field(
        ge=0,
        le=60,
        title="代理授權最多可提前天數",
        description="家長最多可提前幾天建立代理接送授權",
    )
    max_active_per_day: int = Field(
        ge=1,
        le=10,
        title="同一學生同日代理授權上限",
        description="同一位學生同一天可同時有效的代理接送授權筆數",
    )


class PickupPersonsSettings(_SettingModel):
    model_config = ConfigDict(title="常用接送人")

    max_per_student: int = Field(
        ge=1, le=50, title="每位學生常用接送人上限", description="每位學生最多可登記的常用接送人數"
    )


# --- homework ----------------------------------------------------------------------------


class HomeworkDefaults(_SettingModel):
    model_config = ConfigDict(title="作業進度預設")

    auto_reply_without_eta: bool = Field(
        title="未設定預計時間時自動回覆",
        description="家長發起接送而老師尚未設定預計完成時間時，是否自動回覆家長",
    )
    no_eta_reply_text: str = Field(
        min_length=1, max_length=100, title="自動回覆文案", description="自動回覆給家長的文字"
    )


class HomeworkWindow(_SettingModel):
    model_config = ConfigDict(title="作業日期範圍")

    past_days: int = Field(
        ge=0, le=365, title="作業可編輯的過去天數", description="可新增或編輯今天以前幾天的作業"
    )
    future_days: int = Field(
        ge=0, le=60, title="作業可預先新增的天數", description="可預先新增今天以後幾天的作業"
    )


# --- leave -------------------------------------------------------------------------------


class LeaveWindow(_SettingModel):
    model_config = ConfigDict(title="請假申請範圍")

    past_days: int = Field(
        ge=0, le=365, title="家長可申請的過去天數", description="家長可補請今天以前幾天的假"
    )
    future_days: int = Field(
        ge=1, le=365, title="家長可申請的未來天數", description="家長可預先申請今天以後幾天的假"
    )
    max_attachments: int = Field(
        ge=0, le=10, title="每筆請假附件數上限", description="每筆請假最多可上傳的附件數"
    )
    # 上限 10 受 DB-019 size_bytes CHECK 與 BACKEND-016 ATTACHMENT_MAX_BYTES 硬性限制
    # （architecture_decisions §10）
    max_attachment_mb: int = Field(
        ge=1,
        le=10,
        title="單一附件大小上限（MB）",
        description="每個附件的檔案大小上限，最多 10 MB",
    )


# --- notification ------------------------------------------------------------------------

_EVENT_KEYS: Final = frozenset(e.value for e in Event)


class NotificationToggles(RootModel[dict[str, bool]]):
    """key 集合必須恰為 BACKEND-201 的全部事件（以此代替 extra='forbid'）。"""

    model_config = ConfigDict(
        title="通知開關",
        frozen=True,
        json_schema_extra={"x-labels": {e.value: EVENTS[e].label for e in Event}},
    )

    @model_validator(mode="after")
    def _exact_event_keys(self) -> Self:
        keys = set(self.root)
        missing = sorted(_EVENT_KEYS - keys)
        unknown = sorted(keys - _EVENT_KEYS)
        if missing or unknown:
            raise ValueError(f"通知開關的事件必須恰為全部事件：缺少 {missing}、多出 {unknown}")
        return self


# --- line --------------------------------------------------------------------------------


class LineLiff(_SettingModel):
    model_config = ConfigDict(title="LINE 登入（LIFF）")

    liff_id: str = Field(
        pattern=r"^$|^\d+-[A-Za-z0-9]+$",
        title="LIFF 應用程式 ID",
        description="LINE Developers 後台的 LIFF ID，格式如 1657000000-AbcdEfgh；未設定留空",
    )
    channel_id: str = Field(
        pattern=r"^$|^\d{6,20}$",
        title="LINE 登入 Channel ID",
        description="LINE Login channel ID，後端驗證 id_token 用；未設定留空",
    )
    add_friend_url: str = Field(
        default="",
        max_length=300,
        pattern=r"^$|^https://",
        title="官方帳號加好友網址",
        description="家長端引導加入官方帳號的網址，須為 https://；未設定留空",
    )


class LineMessaging(_SettingModel):
    model_config = ConfigDict(title="LINE 訊息推播")

    channel_access_token: str | None = Field(
        default=None,
        max_length=500,
        title="LINE 訊息 Channel Access Token",
        description="Messaging API 的 channel access token，加密存放",
    )
    channel_secret: str | None = Field(
        default=None,
        max_length=500,
        title="LINE 訊息 Channel Secret",
        description="Messaging API 的 channel secret，加密存放",
    )


# --- registry ----------------------------------------------------------------------------


@dataclass(frozen=True)
class SettingDef[M: BaseModel]:
    key: str
    schema: type[M]
    default: M
    is_secret: bool
    secret_fields: tuple[str, ...]  # 加密存放、回傳遮罩的欄位
    group: SettingGroup
    label: str  # 繁中顯示名稱
    public_fields: tuple[str, ...] = ()  # 可經 GET /api/parent/config 公開的欄位


@dataclass(frozen=True)
class SettingKey[M: BaseModel]:
    """型別化 handle：``ORG_PROFILE.definition().default`` 的型別為 ``OrgProfile``。"""

    key: str
    schema: type[M]

    def definition(self) -> SettingDef[M]:
        return cast(SettingDef[M], REGISTRY[self.key])


ORG_PROFILE: Final = SettingKey("org.profile", OrgProfile)
ORG_SERVICE_HOURS: Final = SettingKey("org.service_hours", ServiceHours)
PICKUP_WINDOW: Final = SettingKey("pickup.window", PickupWindow)
HOMEWORK_DEFAULTS: Final = SettingKey("homework.defaults", HomeworkDefaults)
NOTIFICATION_TOGGLES: Final = SettingKey("notification.toggles", NotificationToggles)
LINE_LIFF: Final = SettingKey("line.liff", LineLiff)
LINE_MESSAGING: Final = SettingKey("line.messaging", LineMessaging)
LEAVE_WINDOW: Final = SettingKey("leave.window", LeaveWindow)
PICKUP_AUTHORIZATION: Final = SettingKey("pickup.authorization", PickupAuthorizationSettings)
PICKUP_PERSONS: Final = SettingKey("pickup.persons", PickupPersonsSettings)
HOMEWORK_WINDOW: Final = SettingKey("homework.window", HomeworkWindow)

_WEEKDAY: Final = DayHours(open=True, start="12:00", end="19:00")


def _def[M: BaseModel](
    handle: SettingKey[M],
    default: M,
    *,
    group: SettingGroup,
    label: str,
    public_fields: tuple[str, ...] = (),
    secret_fields: tuple[str, ...] = (),
) -> SettingDef[M]:
    return SettingDef(
        key=handle.key,
        schema=handle.schema,
        default=default,
        is_secret=bool(secret_fields),
        secret_fields=secret_fields,
        group=group,
        label=label,
        public_fields=public_fields,
    )


_DEFS: tuple[SettingDef[Any], ...] = (
    _def(
        ORG_PROFILE,
        OrgProfile(name="", address="", phone="", logo_url=None),
        group="org",
        label="安親班資料",
        public_fields=("name", "phone", "logo_url"),
    ),
    _def(
        ORG_SERVICE_HOURS,
        ServiceHours(
            mon=_WEEKDAY,
            tue=_WEEKDAY,
            wed=_WEEKDAY,
            thu=_WEEKDAY,
            fri=_WEEKDAY,
            sat=DayHours(open=False, start="08:00", end="12:00"),
        ),
        group="org",
        label="營業時段",
    ),
    _def(
        PICKUP_WINDOW,
        PickupWindow(
            request_start="12:00",
            request_end="19:00",
            latest_expected_arrival="19:00",
            auto_expire_minutes=120,
        ),
        group="pickup",
        label="接送時段",
    ),
    _def(
        HOMEWORK_DEFAULTS,
        HomeworkDefaults(
            auto_reply_without_eta=True, no_eta_reply_text="已通知老師，稍後回覆預計時間"
        ),
        group="homework",
        label="作業進度預設",
    ),
    _def(
        NOTIFICATION_TOGGLES,
        NotificationToggles({e.value: True for e in Event}),
        group="notification",
        label="通知開關",
    ),
    _def(
        LINE_LIFF,
        LineLiff(liff_id="", channel_id="", add_friend_url=""),
        group="line",
        label="LINE 登入（LIFF）",
        public_fields=("liff_id", "add_friend_url"),
    ),
    _def(
        LINE_MESSAGING,
        LineMessaging(channel_access_token=None, channel_secret=None),
        group="line",
        label="LINE 訊息推播",
        secret_fields=("channel_access_token", "channel_secret"),
    ),
    _def(
        LEAVE_WINDOW,
        LeaveWindow(past_days=30, future_days=60, max_attachments=3, max_attachment_mb=10),
        group="leave",
        label="請假申請範圍",
        public_fields=("past_days", "future_days", "max_attachments", "max_attachment_mb"),
    ),
    _def(
        PICKUP_AUTHORIZATION,
        PickupAuthorizationSettings(max_days_ahead=14, max_active_per_day=3),
        group="pickup",
        label="代理接送授權",
        public_fields=("max_days_ahead",),
    ),
    _def(
        PICKUP_PERSONS,
        PickupPersonsSettings(max_per_student=10),
        group="pickup",
        label="常用接送人",
        public_fields=("max_per_student",),
    ),
    _def(
        HOMEWORK_WINDOW,
        HomeworkWindow(past_days=30, future_days=7),
        group="homework",
        label="作業日期範圍",
    ),
)

REGISTRY: Final[Mapping[str, SettingDef[Any]]] = MappingProxyType({d.key: d for d in _DEFS})
