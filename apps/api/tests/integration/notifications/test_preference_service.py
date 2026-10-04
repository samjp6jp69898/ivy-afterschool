"""BACKEND-219：app/notifications/preference_service.py（家長 LINE 推播偏好，稀疏列）。"""

from sqlalchemy.orm import Session

from app.models.notifications import NotificationPreference
from app.notifications.events import EVENTS, PARENT_LINE_CONFIGURABLE, Event
from app.notifications.preference_service import get_preferences
from tests.support.factories import make_parent

_EXPECTED_ORDER = [
    "attendance.checked_in",
    "attendance.checked_out",
    "homework.eta_updated",
    "homework.done",
    "pickup.replied",
    "pickup.completed",
    "exam.published",
]


def test_get_preferences_defaults(db_session: Session) -> None:
    parent = make_parent(db_session)

    result = get_preferences(db_session, parent.id)

    assert [i.event for i in result.items] == _EXPECTED_ORDER
    assert [i.event for i in result.items] == [
        e.value for e in Event if e in PARENT_LINE_CONFIGURABLE
    ]
    assert len(result.items) == 7
    assert all(i.line_enabled is True for i in result.items)
    assert [i.label for i in result.items] == [EVENTS[Event(e)].label for e in _EXPECTED_ORDER]
    assert result.items[0].label == "到班通知"


def test_get_preferences_sparse(db_session: Session) -> None:
    parent = make_parent(db_session)
    db_session.add(
        NotificationPreference(
            parent_account_id=parent.id, event="exam.published", line_enabled=False
        )
    )
    db_session.add(
        NotificationPreference(
            parent_account_id=parent.id, event="homework.done", line_enabled=True
        )
    )
    db_session.flush()

    result = get_preferences(db_session, parent.id)

    by_event = {i.event: i.line_enabled for i in result.items}
    assert by_event["exam.published"] is False
    assert [e for e, on in by_event.items() if not on] == ["exam.published"]
    assert [i.event for i in result.items] == _EXPECTED_ORDER


def test_get_preferences_isolated_per_parent(db_session: Session) -> None:
    mine = make_parent(db_session)
    other = make_parent(db_session)
    db_session.add(
        NotificationPreference(
            parent_account_id=other.id, event="attendance.checked_in", line_enabled=False
        )
    )
    db_session.flush()

    assert all(i.line_enabled for i in get_preferences(db_session, mine.id).items)
