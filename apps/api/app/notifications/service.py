"""BACKEND-206：所有業務通知的唯一入口 ``enqueue``（domain_spec M9）。

移植 ivy ``services/notification/dispatch.py::enqueue`` / ``_persist_outbox_intent`` 的「業務交易內
寫入意圖、commit 後才派送、rollback 自動丟棄」；改為教科書 outbox（每個 notification 對 line 一
列），去掉 channels_override、群組推播、sender / source entity。

流程（同一交易內，只 flush 不 commit）：
1. ``notification.toggles``（BACKEND-108）該事件為 false → 回空結果，不寫入、不推播（全域開關只
   影響通知，不影響營運模組各自的業務 ws 廣播）。
2. recipients 去重（保序）；任一 recipient.type 不在 ``EVENTS[event].recipient_types`` →
   ValueError（程式錯誤）。空清單 → 空結果。
3. ``render``（BACKEND-203；缺 payload key → ValueError）；payload 以 JSON 相容形式存入。
4. 每位收件人依 ``channels_for``（BACKEND-202）：含 ``in_app`` → 一列 notifications；含 ``line``
   （必為家長）→ 該家長沒有 ``notification_preferences(line_enabled=false)`` 列時一列
   ``notification_outbox(channel='line', status='pending', next_attempt_at=now)``。
5. flush 後註冊 after-commit（BACKEND-006）：``kick_outbox(outbox_ids)``（BACKEND-209）；有
   notifications 列的收件人推 ``notification.created``（data = NotificationOut）、頻道只有 ``ws``
   的收件人推 ``notification.transient``（data = {event, title, body, payload}），皆經 BACKEND-224
   ``push_to_recipient_after_commit`` 送到個人頻道。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import Clock
from app.core.settings_registry import NOTIFICATION_TOGGLES
from app.core.tx_hooks import run_after_commit
from app.models.notifications import Notification, NotificationOutbox, NotificationPreference
from app.notifications.channel_matrix import channels_for
from app.notifications.events import EVENTS, Event
from app.notifications.outbox_jobs import kick_outbox
from app.notifications.recipients import Recipient
from app.notifications.templates import Rendered, render
from app.realtime.publish import push_to_recipient_after_commit
from app.schemas.notifications import NotificationOut
from app.services.settings_service import get_setting

TYPE_NOTIFICATION_CREATED = "notification.created"
TYPE_NOTIFICATION_TRANSIENT = "notification.transient"


@dataclass(frozen=True)
class EnqueueResult:
    notification_ids: list[UUID] = field(default_factory=list)
    outbox_ids: list[UUID] = field(default_factory=list)


def _event_enabled(session: Session, event: Event) -> bool:
    return get_setting(session, NOTIFICATION_TOGGLES).root.get(event.value, True)


def _unique_recipients(event: Event, recipients: Sequence[Recipient]) -> list[Recipient]:
    allowed = EVENTS[event].recipient_types
    unique = list(dict.fromkeys(recipients))
    invalid = sorted({r.type for r in unique if r.type not in allowed})
    if invalid:
        raise ValueError(f"{event} 不發給 {', '.join(invalid)}（只接受 {sorted(allowed)}）")
    return unique


def _line_disabled_parents(session: Session, event: Event, parent_ids: list[UUID]) -> set[UUID]:
    if not parent_ids:
        return set()
    rows = session.execute(
        select(NotificationPreference.parent_account_id).where(
            NotificationPreference.parent_account_id.in_(parent_ids),
            NotificationPreference.event == event.value,
            NotificationPreference.line_enabled.is_(False),
        )
    )
    return set(rows.scalars())


def _notification_out(row: Notification, rendered: Rendered) -> NotificationOut:
    return NotificationOut(
        id=row.id,
        event=row.event,
        title=row.title,
        body=row.body,
        payload=row.payload,
        read_at=row.read_at,
        created_at=row.created_at,
        deep_link=rendered.deep_link,
    )


def enqueue(
    session: Session,
    event: Event,
    *,
    recipients: Sequence[Recipient],
    payload: Mapping[str, Any],
    clock: Clock,
) -> EnqueueResult:
    if not _event_enabled(session, event):
        return EnqueueResult()
    unique = _unique_recipients(event, recipients)
    if not unique:
        return EnqueueResult()

    rendered = render(event, payload)
    json_payload: dict[str, Any] = jsonable_encoder(dict(payload))
    now = clock.now()

    line_disabled = _line_disabled_parents(
        session,
        event,
        [r.id for r in unique if r.type == "parent" and "line" in channels_for(event, r.type)],
    )

    notifications: list[tuple[Recipient, Notification]] = []
    transient_recipients: list[Recipient] = []
    outbox_rows: list[NotificationOutbox] = []
    for recipient in unique:
        channels = channels_for(event, recipient.type)
        if "in_app" not in channels:
            transient_recipients.append(recipient)
            continue
        row = Notification(
            created_at=now,
            recipient_type=recipient.type,
            recipient_id=recipient.id,
            event=event.value,
            title=rendered.title,
            body=rendered.body,
            payload=json_payload,
        )
        session.add(row)
        notifications.append((recipient, row))
    session.flush()
    for recipient, row in notifications:
        if "line" in channels_for(event, recipient.type) and recipient.id not in line_disabled:
            outbox = NotificationOutbox(
                notification_id=row.id, channel="line", status="pending", next_attempt_at=now
            )
            session.add(outbox)
            outbox_rows.append(outbox)
    session.flush()

    outbox_ids = [o.id for o in outbox_rows]
    if outbox_ids:
        run_after_commit(session, lambda: kick_outbox(outbox_ids))
    for recipient, row in notifications:
        push_to_recipient_after_commit(
            session,
            recipient,
            TYPE_NOTIFICATION_CREATED,
            _notification_out(row, rendered).model_dump(),
            clock=clock,
        )
    transient_data = {
        "event": event.value,
        "title": rendered.title,
        "body": rendered.body,
        "payload": json_payload,
    }
    for recipient in transient_recipients:
        push_to_recipient_after_commit(
            session, recipient, TYPE_NOTIFICATION_TRANSIENT, transient_data, clock=clock
        )
    return EnqueueResult(
        notification_ids=[row.id for _, row in notifications], outbox_ids=outbox_ids
    )
