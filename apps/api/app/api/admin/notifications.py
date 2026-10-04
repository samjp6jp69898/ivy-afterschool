"""BACKEND-214：後台個人收件匣 endpoint（只需登入，不掛權限碼；route audit 白名單）。

``GET /api/admin/notifications``：只回自己的站內通知（Recipient('staff', staff.id)）；強制改密碼
的帳號由 ``get_current_staff`` 擋下（403 password_change_required）。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.admin._query import query_model
from app.api.deps import CurrentStaff, get_current_staff
from app.core.db import get_db
from app.core.pagination import PageParams, page_params
from app.notifications import inbox_service
from app.notifications.recipients import Recipient
from app.schemas.notifications import NotificationListQuery, NotificationPageOut

router = APIRouter(prefix="/notifications", tags=["admin-notifications"])


@router.get("", response_model=NotificationPageOut)
def list_notifications(
    staff: Annotated[CurrentStaff, Depends(get_current_staff)],
    query: Annotated[NotificationListQuery, Depends(query_model(NotificationListQuery))],
    page: Annotated[PageParams, Depends(page_params)],
    db: Annotated[Session, Depends(get_db)],
) -> NotificationPageOut:
    return inbox_service.list_notifications(db, Recipient("staff", staff.id), query, page)
