"""BACKEND-217：家長端站內通知 endpoint。

``GET /api/parent/notifications``：只回自己的通知（Recipient('parent', parent.id)），別人的通知
不會出現在列表（IDOR）。篩選 / 分頁參數驗證沿用後台列表的 ``query_model``。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.admin._query import query_model
from app.api.deps import CurrentParent, get_current_parent
from app.core.db import get_db
from app.core.pagination import PageParams, page_params
from app.notifications import inbox_service
from app.notifications.recipients import Recipient
from app.schemas.notifications import NotificationListQuery, NotificationPageOut

router = APIRouter(tags=["parent-notifications"])


@router.get("/notifications", response_model=NotificationPageOut)
def list_notifications(
    parent: Annotated[CurrentParent, Depends(get_current_parent)],
    query: Annotated[NotificationListQuery, Depends(query_model(NotificationListQuery))],
    page: Annotated[PageParams, Depends(page_params)],
    db: Annotated[Session, Depends(get_db)],
) -> NotificationPageOut:
    return inbox_service.list_notifications(db, Recipient("parent", parent.id), query, page)
