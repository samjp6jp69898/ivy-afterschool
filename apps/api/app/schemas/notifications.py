"""BACKEND-210：通知中心與家長 LINE 偏好 schemas。合法 event 由 service 驗證。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Self
from uuid import UUID

from pydantic import Field, model_validator

from app.schemas.common import OutModel, RequestModel


class NotificationOut(OutModel):
    id: UUID
    event: str
    title: str
    body: str
    payload: dict[str, Any]
    read_at: datetime | None
    created_at: datetime
    deep_link: str  # 依 event 以 DEEP_LINKS 即時計算，不存 DB


class NotificationListQuery(RequestModel):
    unread_only: bool = False


class NotificationPageOut(OutModel):
    items: list[NotificationOut]
    total: int
    unread_count: int


class MarkAllReadOut(OutModel):
    updated: int


class PreferenceItemOut(OutModel):
    event: str
    label: str
    line_enabled: bool


class PreferencesOut(OutModel):
    items: list[PreferenceItemOut]  # 依 PARENT_LINE_CONFIGURABLE 固定順序


class PreferenceItemIn(RequestModel):
    event: str
    line_enabled: bool


class PreferencesPutIn(RequestModel):
    items: list[PreferenceItemIn] = Field(min_length=1, max_length=7)

    @model_validator(mode="after")
    def _unique_events(self) -> Self:
        events = [item.event for item in self.items]
        if len(set(events)) != len(events):
            raise ValueError("event 不可重複")
        return self
