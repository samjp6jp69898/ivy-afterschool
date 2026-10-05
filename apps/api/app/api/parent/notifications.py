"""BACKEND-217：家長端站內通知 endpoint。

``GET /api/parent/notifications``：只回自己的通知（Recipient('parent', parent.id)），別人的通知
不會出現在列表（IDOR）。篩選 / 分頁參數驗證沿用後台列表的 ``query_model``。

BACKEND-218 ``POST /notifications/{notification_id}/read`` → BACKEND-212 ``mark_read``（別人的通知與
不存在 id 回相同 404，不洩漏存在與否）→ commit → ``NotificationOut``。
BACKEND-221 ``GET /notification-preferences`` → BACKEND-219 ``get_preferences`` → ``PreferencesOut``
（七項 LINE 可設定事件，只反映自己的設定）。
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.admin._query import query_model
from app.api.deps import CurrentParent, get_current_parent
from app.core.clock import Clock, get_clock
from app.core.db import get_db
from app.core.pagination import PageParams, page_params
from app.notifications import inbox_service, preference_service
from app.notifications.recipients import Recipient
from app.schemas.notifications import (
    NotificationListQuery,
    NotificationOut,
    NotificationPageOut,
    PreferencesOut,
)

router = APIRouter(tags=["parent-notifications"])


@router.get("/notifications", response_model=NotificationPageOut)
def list_notifications(
    parent: Annotated[CurrentParent, Depends(get_current_parent)],
    query: Annotated[NotificationListQuery, Depends(query_model(NotificationListQuery))],
    page: Annotated[PageParams, Depends(page_params)],
    db: Annotated[Session, Depends(get_db)],
) -> NotificationPageOut:
    return inbox_service.list_notifications(db, Recipient("parent", parent.id), query, page)


@router.post("/notifications/{notification_id}/read", response_model=NotificationOut)
def mark_read(
    notification_id: UUID,
    parent: Annotated[CurrentParent, Depends(get_current_parent)],
    db: Annotated[Session, Depends(get_db)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> NotificationOut:
    out = inbox_service.mark_read(db, Recipient("parent", parent.id), notification_id, clock=clock)
    db.commit()
    return out


@router.get("/notification-preferences", response_model=PreferencesOut)
def get_preferences(
    parent: Annotated[CurrentParent, Depends(get_current_parent)],
    db: Annotated[Session, Depends(get_db)],
) -> PreferencesOut:
    return preference_service.get_preferences(db, parent.id)
