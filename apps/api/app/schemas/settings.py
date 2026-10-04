"""BACKEND-111：系統設定 schemas。內層 value 由 settings_registry 的 schema 驗證。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.settings_registry import SettingGroup
from app.schemas.common import OutModel, RequestModel


class SettingPutIn(RequestModel):
    value: dict[str, Any]


class SettingOut(OutModel):
    key: str
    group: SettingGroup
    label: str
    is_secret: bool
    value: dict[str, Any]
    json_schema: dict[str, Any]
    updated_at: datetime | None
    updated_by_name: str | None


class SettingsListOut(OutModel):
    items: list[SettingOut]
