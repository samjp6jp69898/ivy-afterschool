"""BACKEND-214：後台個人收件匣 endpoint（只需登入，不掛權限碼；route audit 白名單）。

``GET /api/admin/notifications``：只回自己的站內通知（Recipient('staff', staff.id)）；強制改密碼
的帳號由 ``get_current_staff`` 擋下（403 password_change_required）。

BACKEND-216 ``POST /read-all``（註冊在 ``/{notification_id}/read`` 之前）→ BACKEND-213
``mark_all_read`` → commit → ``MarkAllReadOut``。
BACKEND-215 ``POST /{notification_id}/read`` → BACKEND-212 ``mark_read``（別人的通知與不存在皆 404
``notification_not_found``）→ commit → ``NotificationOut``。
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.admin._query import query_model
from app.api.deps import CurrentStaff, get_current_staff
from app.core.clock import Clock, get_clock
from app.core.db import get_db
from app.core.pagination import PageParams, page_params
from app.notifications import inbox_service
from app.notifications.recipients import Recipient
from app.schemas.notifications import (
    MarkAllReadOut,
    NotificationListQuery,
    NotificationOut,
    NotificationPageOut,
)

router = APIRouter(prefix="/notifications", tags=["admin-notifications"])


@router.get("", response_model=NotificationPageOut)
def list_notifications(
    staff: Annotated[CurrentStaff, Depends(get_current_staff)],
    query: Annotated[NotificationListQuery, Depends(query_model(NotificationListQuery))],
    page: Annotated[PageParams, Depends(page_params)],
    db: Annotated[Session, Depends(get_db)],
) -> NotificationPageOut:
    return inbox_service.list_notifications(db, Recipient("staff", staff.id), query, page)


@router.post("/read-all", response_model=MarkAllReadOut)
def mark_all_read(
    staff: Annotated[CurrentStaff, Depends(get_current_staff)],
    db: Annotated[Session, Depends(get_db)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> MarkAllReadOut:
    out = inbox_service.mark_all_read(db, Recipient("staff", staff.id), clock=clock)
    db.commit()
    return out


@router.post("/{notification_id}/read", response_model=NotificationOut)
def mark_read(
    notification_id: UUID,
    staff: Annotated[CurrentStaff, Depends(get_current_staff)],
    db: Annotated[Session, Depends(get_db)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> NotificationOut:
    out = inbox_service.mark_read(db, Recipient("staff", staff.id), notification_id, clock=clock)
    db.commit()
    return out
