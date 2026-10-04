"""BACKEND-200：通知 models（DB-029 notifications、DB-030 notification_outbox、
DB-031 notification_preferences）。

欄位與 migration 完全一致（BACKEND-024 drift 把關）；event 值域等 CHECK 與 partial index 只在
migration。recipient_type / recipient_id 是多型參照（staff_users / parent_accounts），不建 FK。
NotificationPreference 為稀疏列：沒有列 = 預設開啟。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import ForeignKey, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPkMixin

RecipientType = Literal["staff", "parent"]
OutboxChannel = Literal["line"]
OutboxStatus = Literal["pending", "sent", "failed", "dead"]


class Notification(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "notifications"

    recipient_type: Mapped[RecipientType] = mapped_column(Text)
    recipient_id: Mapped[UUID]
    event: Mapped[str]
    title: Mapped[str]
    body: Mapped[str]
    payload: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
    read_at: Mapped[datetime | None]


class NotificationOutbox(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "notification_outbox"
    # migration 的名稱不是 naming convention 產生的，明確指定
    __table_args__ = (
        UniqueConstraint(
            "notification_id", "channel", name="uq_notification_outbox_notification_channel"
        ),
    )

    notification_id: Mapped[UUID] = mapped_column(
        ForeignKey("notifications.id", ondelete="CASCADE")
    )
    channel: Mapped[OutboxChannel] = mapped_column(Text, server_default="line")
    status: Mapped[OutboxStatus] = mapped_column(Text, server_default="pending")
    attempts: Mapped[int] = mapped_column(server_default="0")
    next_attempt_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    last_error: Mapped[str | None]

    notification: Mapped[Notification] = relationship(lazy="joined")


class NotificationPreference(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "notification_preferences"
    __table_args__ = (
        UniqueConstraint(
            "parent_account_id", "event", name="uq_notification_preferences_parent_event"
        ),
    )

    parent_account_id: Mapped[UUID] = mapped_column(
        ForeignKey("parent_accounts.id", ondelete="CASCADE")
    )
    event: Mapped[str]
    line_enabled: Mapped[bool] = mapped_column(server_default="true")
