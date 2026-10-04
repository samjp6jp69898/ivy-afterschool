"""BACKEND-211：站內通知（員工與家長共用，以 Recipient 區分）。"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.pagination import PageParams, paginate
from app.models.notifications import Notification
from app.notifications.events import Event
from app.notifications.recipients import Recipient
from app.notifications.templates import DEEP_LINKS
from app.schemas.notifications import NotificationListQuery, NotificationOut, NotificationPageOut

_DEFAULT_DEEP_LINK = "/"


def _deep_link(event: str) -> str:
    # deep_link 不存 DB，依 event 即時計算；DB CHECK 已限制 event 值域，未知值退回首頁
    try:
        return DEEP_LINKS[Event(event)]
    except ValueError:
        return _DEFAULT_DEEP_LINK


def list_notifications(
    session: Session,
    recipient: Recipient,
    query: NotificationListQuery,
    page: PageParams,
) -> NotificationPageOut:
    """只查自己的列；排序 created_at desc。

    unread_count 為同一收件人全部未讀數（不受篩選與分頁影響）。
    """
    own = (
        Notification.recipient_type == recipient.type,
        Notification.recipient_id == recipient.id,
    )
    stmt = select(Notification).where(*own)
    if query.unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    rows, total = paginate(
        session, stmt.order_by(Notification.created_at.desc(), Notification.id.desc()), page
    )
    unread_count = session.execute(
        select(func.count()).select_from(Notification).where(*own, Notification.read_at.is_(None))
    ).scalar_one()
    return NotificationPageOut(
        items=[
            NotificationOut(
                id=row.id,
                event=row.event,
                title=row.title,
                body=row.body,
                payload=row.payload,
                read_at=row.read_at,
                created_at=row.created_at,
                deep_link=_deep_link(row.event),
            )
            for row in rows
        ],
        total=total,
        unread_count=unread_count,
    )
