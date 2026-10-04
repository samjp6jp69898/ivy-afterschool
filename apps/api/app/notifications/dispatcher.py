"""BACKEND-208：outbox 派送（OutboxDispatcher.dispatch_due）。

移植 ivy ``services/notification/retry_scheduler.py::tick_line_retry`` 的退避表與
``outbox_sweeper.py::tick_outbox_sweep`` 的撿漏；改為以 ``notification_outbox`` 為唯一工作來源。

- 撿取：``status in ('pending','failed') and next_attempt_at <= now [and id in ids]``，依
  ``next_attempt_at`` 排序、``limit``、``for update skip locked``（多個 dispatcher 並行時同一列
  只有一條連線處理；持鎖期間送 LINE，另一條直接跳過不重送）。
- 每列：家長不存在 / 停用 / 收件者不是家長 → dead ``parent_inactive``；``line_client`` 為 None →
  dead ``line_not_configured``（未設定 LINE 時不累積待送，attempts 不加）。
- 送出 ``line_text(Rendered(title, body, deep_link), public_base_url)``，retry_key = outbox id
  （LINE 端去重）。ok → sent；retryable → ``attempts += 1``，達 ``MAX_ATTEMPTS`` 轉 dead，否則
  failed 且 ``next_attempt_at = now + BACKOFF[attempts - 1]``；不可重試 → dead。
- ``last_error`` 截 2000 字；failed / dead 一定有 last_error（DB CHECK），client 沒給錯誤訊息時補
  ``unknown_error``。
- 只 flush 不 commit：呼叫端（BACKEND-209 的 job runner 或 kick 的 session_scope）commit。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import Clock
from app.core.config import Settings
from app.models.notifications import NotificationOutbox
from app.models.parents import ParentAccount
from app.notifications.channels.line import ERROR_MAX_CHARS, LineMessagingClient
from app.notifications.events import Event
from app.notifications.templates import DEEP_LINKS, Rendered, line_text

logger = logging.getLogger(__name__)

BACKOFF: Final = (
    timedelta(seconds=30),
    timedelta(minutes=2),
    timedelta(minutes=10),
    timedelta(minutes=30),
    timedelta(hours=2),
)
MAX_ATTEMPTS: Final = 5
ERROR_PARENT_INACTIVE: Final = "parent_inactive"
ERROR_LINE_NOT_CONFIGURED: Final = "line_not_configured"
_UNKNOWN_ERROR: Final = "unknown_error"


@dataclass(frozen=True)
class DispatchStats:
    sent: int
    retried: int
    dead: int


def _due_rows(
    session: Session, *, now: datetime, limit: int, ids: Sequence[UUID] | None
) -> list[NotificationOutbox]:
    if ids is not None and not ids:
        return []
    stmt = (
        select(NotificationOutbox)
        .where(
            NotificationOutbox.status.in_(("pending", "failed")),
            NotificationOutbox.next_attempt_at <= now,
        )
        .order_by(NotificationOutbox.next_attempt_at, NotificationOutbox.id)
        .limit(limit)
        .with_for_update(skip_locked=True, of=NotificationOutbox)
    )
    if ids is not None:
        stmt = stmt.where(NotificationOutbox.id.in_(list(ids)))
    return list(session.execute(stmt).scalars().unique())


def _mark_dead(row: NotificationOutbox, error: str) -> None:
    row.status = "dead"
    row.last_error = error[:ERROR_MAX_CHARS]


def _load_active_parent(session: Session, row: NotificationOutbox) -> ParentAccount | None:
    notification = row.notification
    if notification.recipient_type != "parent":
        return None
    parent = session.get(ParentAccount, notification.recipient_id)
    if parent is None or parent.status == "disabled":
        return None
    return parent


def _text_for(row: NotificationOutbox, settings: Settings) -> str:
    notification = row.notification
    rendered = Rendered(
        title=notification.title,
        body=notification.body,
        deep_link=DEEP_LINKS[Event(notification.event)],
    )
    return line_text(rendered, public_base_url=settings.public_base_url_str)


def dispatch_due(
    session: Session,
    *,
    clock: Clock,
    settings: Settings,
    line_client: LineMessagingClient | None,
    limit: int = 50,
    ids: Sequence[UUID] | None = None,
) -> DispatchStats:
    now = clock.now()
    sent = retried = dead = 0
    for row in _due_rows(session, now=now, limit=limit, ids=ids):
        parent = _load_active_parent(session, row)
        if parent is None:
            _mark_dead(row, ERROR_PARENT_INACTIVE)
            dead += 1
            continue
        if line_client is None:
            _mark_dead(row, ERROR_LINE_NOT_CONFIGURED)
            dead += 1
            continue

        result = line_client.push_text(
            to=parent.line_user_id, text=_text_for(row, settings), retry_key=row.id
        )
        row.attempts += 1
        if result.ok:
            row.status = "sent"
            row.last_error = None
            sent += 1
            continue
        error = result.error or _UNKNOWN_ERROR
        if not result.retryable or row.attempts >= MAX_ATTEMPTS:
            _mark_dead(row, error)
            dead += 1
            logger.warning(
                "outbox %s 轉 dead attempts=%s retryable=%s", row.id, row.attempts, result.retryable
            )
            continue
        row.status = "failed"
        row.last_error = error[:ERROR_MAX_CHARS]
        row.next_attempt_at = now + BACKOFF[row.attempts - 1]
        retried += 1
    session.flush()
    return DispatchStats(sent=sent, retried=retried, dead=dead)
