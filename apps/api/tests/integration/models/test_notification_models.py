"""BACKEND-200：app/models/notifications.py（Notification、NotificationOutbox、NotificationPreference）。"""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.notifications import Notification, NotificationOutbox, NotificationPreference
from app.models.parents import ParentAccount


def _make_parent(db_session: Session) -> ParentAccount:
    parent = ParentAccount(line_user_id="U" + uuid4().hex, display_name="王媽媽")
    db_session.add(parent)
    db_session.flush()
    return parent


def _make_notification(db_session: Session, parent: ParentAccount, **kwargs: str) -> Notification:
    fields = {"event": "homework.done", **kwargs}
    notification = Notification(
        recipient_type="parent",
        recipient_id=parent.id,
        title="王小明 作業已完成",
        body="作業已完成，可以來接送了。",
        **fields,
    )
    db_session.add(notification)
    db_session.flush()
    return notification


def test_notification_models_defaults(db_session: Session) -> None:
    parent = _make_parent(db_session)
    notification = _make_notification(db_session, parent)
    db_session.refresh(notification)

    assert isinstance(notification.id, UUID)
    assert notification.payload == {}
    assert notification.read_at is None
    assert notification.recipient_id == parent.id

    outbox = NotificationOutbox(notification_id=notification.id)
    preference = NotificationPreference(parent_account_id=parent.id, event="pickup.replied")
    db_session.add_all([outbox, preference])
    db_session.flush()
    db_session.refresh(outbox)
    db_session.refresh(preference)

    assert outbox.channel == "line"
    assert outbox.status == "pending"
    assert outbox.attempts == 0
    assert outbox.last_error is None
    assert outbox.next_attempt_at.tzinfo is not None
    assert preference.line_enabled is True

    db_session.expunge_all()
    loaded = db_session.execute(
        select(NotificationOutbox).where(NotificationOutbox.id == outbox.id)
    ).scalar_one()
    assert NotificationOutbox.notification.property.lazy == "joined"
    assert "notification" not in inspect(loaded).unloaded
    assert loaded.notification.title == "王小明 作業已完成"
    assert loaded.notification.event == "homework.done"


def test_notification_models_payload_roundtrip(db_session: Session) -> None:
    parent = _make_parent(db_session)
    notification = Notification(
        recipient_type="parent",
        recipient_id=parent.id,
        event="pickup.replied",
        title="接送回覆",
        body="老師已回覆接送請求。",
        payload={"student_id": str(uuid4()), "eta_minutes": 10},
    )
    db_session.add(notification)
    db_session.flush()
    db_session.expunge_all()

    loaded = db_session.execute(
        select(Notification).where(Notification.id == notification.id)
    ).scalar_one()
    assert loaded.payload["eta_minutes"] == 10
    assert set(loaded.payload) == {"student_id", "eta_minutes"}


def test_notification_models_event_check(db_session: Session) -> None:
    parent = _make_parent(db_session)

    with pytest.raises(IntegrityError) as excinfo:
        _make_notification(db_session, parent, event="foo.bar")
    assert getattr(excinfo.value.orig, "sqlstate", None) == "23514"
    diag = getattr(excinfo.value.orig, "diag", None)
    assert getattr(diag, "constraint_name", None) == "ck_notifications_event"


def test_notification_models_preference_unique(db_session: Session) -> None:
    parent = _make_parent(db_session)
    db_session.add(
        NotificationPreference(
            parent_account_id=parent.id, event="homework.done", line_enabled=False
        )
    )
    db_session.flush()

    db_session.add(NotificationPreference(parent_account_id=parent.id, event="homework.done"))
    with pytest.raises(IntegrityError) as excinfo:
        db_session.flush()
    assert getattr(excinfo.value.orig, "sqlstate", None) == "23505"
    diag = getattr(excinfo.value.orig, "diag", None)
    assert getattr(diag, "constraint_name", None) == "uq_notification_preferences_parent_event"
