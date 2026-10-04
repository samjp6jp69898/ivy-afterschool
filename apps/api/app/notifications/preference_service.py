"""BACKEND-219：家長 LINE 推播偏好（稀疏列：沒有列 = 預設開啟；in_app 一律開啟，不在偏好內）。"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.notifications import NotificationPreference
from app.notifications.events import EVENTS, PARENT_LINE_CONFIGURABLE, Event
from app.schemas.notifications import PreferenceItemOut, PreferencesOut


def get_preferences(session: Session, parent_id: UUID) -> PreferencesOut:
    rows = session.execute(
        select(NotificationPreference.event, NotificationPreference.line_enabled).where(
            NotificationPreference.parent_account_id == parent_id
        )
    )
    stored = {event: line_enabled for event, line_enabled in rows}
    # 順序為 Event 定義順序（與 events._DEFS 一致）
    return PreferencesOut(
        items=[
            PreferenceItemOut(
                event=event.value,
                label=EVENTS[event].label,
                line_enabled=stored.get(event.value, True),
            )
            for event in Event
            if event in PARENT_LINE_CONFIGURABLE
        ]
    )
