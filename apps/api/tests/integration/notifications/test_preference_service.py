"""BACKEND-219：app/notifications/preference_service.py（家長 LINE 推播偏好，稀疏列）。"""

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models.notifications import NotificationPreference
from app.notifications.events import EVENTS, PARENT_LINE_CONFIGURABLE, Event
from app.notifications.preference_service import get_preferences, put_preferences
from app.schemas.notifications import PreferencesPutIn
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


def _put(*pairs: tuple[str, bool]) -> PreferencesPutIn:
    return PreferencesPutIn.model_validate(
        {"items": [{"event": e, "line_enabled": on} for e, on in pairs]}
    )


def _row_count(db: Session, parent_id: object) -> int:
    return db.execute(
        select(func.count())
        .select_from(NotificationPreference)
        .where(NotificationPreference.parent_account_id == parent_id)
    ).scalar_one()


def test_put_preferences_partial(db_session: Session) -> None:
    parent = make_parent(db_session)
    data = _put(("homework.done", False))

    result = put_preferences(db_session, parent.id, data)

    by_event = {i.event: i.line_enabled for i in result.items}
    assert by_event["homework.done"] is False
    assert [e for e, on in by_event.items() if on] == [e for e in by_event if e != "homework.done"]
    assert len(result.items) == 7

    again = put_preferences(db_session, parent.id, data)
    assert again == result
    assert _row_count(db_session, parent.id) == 1


def test_put_preferences_updates_existing_value(db_session: Session) -> None:
    parent = make_parent(db_session)
    put_preferences(
        db_session, parent.id, _put(("exam.published", False), ("homework.done", False))
    )

    result = put_preferences(db_session, parent.id, _put(("exam.published", True)))

    by_event = {i.event: i.line_enabled for i in result.items}
    assert by_event["exam.published"] is True
    assert by_event["homework.done"] is False  # 沒送的事件不受影響
    assert _row_count(db_session, parent.id) == 2
    assert get_preferences(db_session, parent.id) == result


def test_put_preferences_only_affects_own_rows(db_session: Session) -> None:
    mine, other = make_parent(db_session), make_parent(db_session)
    put_preferences(db_session, other.id, _put(("homework.done", False)))

    put_preferences(db_session, mine.id, _put(("homework.done", True)))

    assert all(i.line_enabled for i in get_preferences(db_session, mine.id).items) is True
    other_prefs = {i.event: i.line_enabled for i in get_preferences(db_session, other.id).items}
    assert other_prefs["homework.done"] is False


@pytest.mark.parametrize("event", ["binding.completed", "leave.created", "no.such_event"])
def test_put_preferences_invalid_event(db_session: Session, event: str) -> None:
    parent = make_parent(db_session)

    with pytest.raises(AppError) as exc:
        put_preferences(db_session, parent.id, _put(("homework.done", False), (event, False)))

    assert (exc.value.status, exc.value.code) == (422, "invalid_preference_event")
    assert exc.value.details == {"events": [event]}
    # 整批拒絕：合法的那筆也沒有寫入
    assert _row_count(db_session, parent.id) == 0
