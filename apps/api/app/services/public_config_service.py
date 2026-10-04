"""BACKEND-124：家長端公開設定（GET /api/parent/config 的資料來源）。

只讀 registry 標記 ``public_fields`` 的欄位（``PUBLIC_SOURCES`` 逐欄對應，有一致性測試），透過
BACKEND-108 ``get_setting``（走 cache）。輸出 model 欄位固定，絕不包含任何 secret 欄位：即使日後在
registry 誤把 secret 欄位標成 public，本模組也不會把它帶出去。
"""

from __future__ import annotations

from typing import Final

from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.core.settings_registry import (
    LEAVE_WINDOW,
    LINE_LIFF,
    ORG_PROFILE,
    PICKUP_AUTHORIZATION,
    PICKUP_PERSONS,
)
from app.services.settings_service import get_setting


class PublicLimitsOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    leave_past_days: int
    leave_future_days: int
    leave_max_attachments: int
    leave_max_attachment_mb: int
    authorization_max_days_ahead: int
    persons_max: int


class PublicConfigOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    liff_id: str
    add_friend_url: str | None  # 空字串轉 None
    org_name: str
    org_phone: str
    logo_url: str | None
    limits: PublicLimitsOut


# 輸出欄位 → (system_settings key, registry 欄位名)；每一項都必須在該 key 的 public_fields 內
PUBLIC_SOURCES: Final[dict[str, tuple[str, str]]] = {
    "org_name": (ORG_PROFILE.key, "name"),
    "org_phone": (ORG_PROFILE.key, "phone"),
    "logo_url": (ORG_PROFILE.key, "logo_url"),
    "liff_id": (LINE_LIFF.key, "liff_id"),
    "add_friend_url": (LINE_LIFF.key, "add_friend_url"),
    "limits.leave_past_days": (LEAVE_WINDOW.key, "past_days"),
    "limits.leave_future_days": (LEAVE_WINDOW.key, "future_days"),
    "limits.leave_max_attachments": (LEAVE_WINDOW.key, "max_attachments"),
    "limits.leave_max_attachment_mb": (LEAVE_WINDOW.key, "max_attachment_mb"),
    "limits.authorization_max_days_ahead": (PICKUP_AUTHORIZATION.key, "max_days_ahead"),
    "limits.persons_max": (PICKUP_PERSONS.key, "max_per_student"),
}


def get_public_config(session: Session) -> PublicConfigOut:
    profile = get_setting(session, ORG_PROFILE)
    liff = get_setting(session, LINE_LIFF)
    leave = get_setting(session, LEAVE_WINDOW)
    authorization = get_setting(session, PICKUP_AUTHORIZATION)
    persons = get_setting(session, PICKUP_PERSONS)
    return PublicConfigOut(
        liff_id=liff.liff_id,
        add_friend_url=liff.add_friend_url or None,
        org_name=profile.name,
        org_phone=profile.phone,
        logo_url=None if profile.logo_url is None else str(profile.logo_url),
        limits=PublicLimitsOut(
            leave_past_days=leave.past_days,
            leave_future_days=leave.future_days,
            leave_max_attachments=leave.max_attachments,
            leave_max_attachment_mb=leave.max_attachment_mb,
            authorization_max_days_ahead=authorization.max_days_ahead,
            persons_max=persons.max_per_student,
        ),
    )
