"""BACKEND-219：家長 LINE 推播偏好（稀疏列：沒有列 = 預設開啟；in_app 一律開啟，不在偏好內）。"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models.notifications import NotificationPreference
from app.notifications.events import EVENTS, PARENT_LINE_CONFIGURABLE, Event
from app.schemas.notifications import PreferenceItemOut, PreferencesOut, PreferencesPutIn


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


def put_preferences(session: Session, parent_id: UUID, data: PreferencesPutIn) -> PreferencesOut:
    """部分更新：只處理有送的事件（upsert）；任一事件不可設定 → 422，整批不寫入。"""
    allowed = {event.value for event in PARENT_LINE_CONFIGURABLE}
    invalid = [item.event for item in data.items if item.event not in allowed]
    if invalid:
        raise AppError(
            "invalid_preference_event",
            "包含不可設定的通知事件",
            status=422,
            details={"events": invalid},
        )
    stmt = insert(NotificationPreference).values(
        [
            {
                "parent_account_id": parent_id,
                "event": item.event,
                "line_enabled": item.line_enabled,
            }
            for item in data.items
        ]
    )
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=["parent_account_id", "event"],
            set_={"line_enabled": stmt.excluded.line_enabled},
        )
    )
    return get_preferences(session, parent_id)
