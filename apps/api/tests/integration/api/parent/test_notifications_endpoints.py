"""BACKEND-217：GET /api/parent/notifications。
BACKEND-218：POST /api/parent/notifications/{id}/read。
BACKEND-221：GET /api/parent/notification-preferences。
BACKEND-222：PUT /api/parent/notification-preferences（之後該事件不再建立 outbox）。
BACKEND-520：POST /api/parent/notifications/read-all（只標記自己的）。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.account import StaffUser
from app.models.notifications import Notification, NotificationPreference
from app.models.parents import ParentAccount
from app.notifications.events import EVENTS, PARENT_LINE_CONFIGURABLE, Event
from app.notifications.recipients import Recipient
from app.notifications.service import enqueue
from tests.support.factories import make_staff
from tests.support.fake_clock import FakeClock

_URL = "/api/parent/notifications"
ParentClientFactory = Callable[..., tuple[TestClient, ParentAccount]]
StaffClientFactory = Callable[..., tuple[TestClient, StaffUser]]
AssertError = Callable[..., None]


def _note(
    db: Session,
    recipient_id: UUID,
    *,
    title: str = "王小明 作業已完成",
    event: str = "homework.done",
    read: bool = False,
    hour: int = 8,
    recipient_type: str = "parent",
) -> Notification:
    row = Notification(
        recipient_type=recipient_type,
        recipient_id=recipient_id,
        event=event,
        title=title,
        body="王小明 的作業已全部完成。",
        payload={"student_id": str(uuid4())},
        read_at=datetime(2026, 9, 1, 12, tzinfo=UTC) if read else None,
        created_at=datetime(2026, 9, 1, hour, tzinfo=UTC),
    )
    db.add(row)
    db.flush()
    return row


def test_parent_notifications_list_success(
    parent_client: ParentClientFactory, db_session: Session
) -> None:
    client, parent = parent_client()
    _note(db_session, parent.id)
    db_session.commit()

    resp = client.get(_URL)

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["unread_count"] == 1
    assert body["items"][0]["title"] == "王小明 作業已完成"
    assert body["items"][0]["event"] == "homework.done"
    assert body["items"][0]["deep_link"] == "/homework"
    assert body["items"][0]["read_at"] is None


def test_parent_notifications_list_422(
    parent_client: ParentClientFactory, assert_error: AssertError
) -> None:
    client, _ = parent_client()

    for params in ({"page": 0}, {"page_size": 201}, {"unread_only": "maybe"}, {"foo": "bar"}):
        assert_error(client.get(_URL, params=params), 422, "validation_error")


def test_parent_notifications_list_401(
    api_client: TestClient, staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    assert_error(api_client.get(_URL), 401, "unauthenticated")
    staff, _ = staff_client(permissions=["students:read"])
    assert_error(staff.get(_URL), 401, "unauthenticated")


def test_parent_notifications_list_idor(
    parent_client: ParentClientFactory, db_session: Session
) -> None:
    client_a, parent_a = parent_client()
    _, parent_b = parent_client()
    _note(db_session, parent_a.id, title="A 的通知")
    _note(db_session, parent_b.id, title="B 的通知")
    _note(db_session, parent_a.id, title="同 id 的員工通知", recipient_type="staff")
    db_session.commit()

    resp = client_a.get(_URL)

    assert [i["title"] for i in resp.json()["items"]] == ["A 的通知"]
    assert resp.json()["total"] == 1
    assert "B 的通知" not in resp.text
    assert "同 id 的員工通知" not in resp.text


def test_parent_notifications_list_unread_only(
    parent_client: ParentClientFactory, db_session: Session
) -> None:
    client, parent = parent_client()
    _note(db_session, parent.id, title="已讀", read=True, hour=7)
    _note(db_session, parent.id, title="未讀", hour=9)
    db_session.commit()

    resp = client.get(_URL, params={"unread_only": "true"})

    body = resp.json()
    assert [i["title"] for i in body["items"]] == ["未讀"]
    assert body["total"] == 1
    assert body["unread_count"] == 1
    assert client.get(_URL).json()["total"] == 2


def test_parent_notifications_list_disabled_parent(
    parent_client: ParentClientFactory, assert_error: AssertError
) -> None:
    client, _ = parent_client(status="disabled")

    assert_error(client.get(_URL), 401, "unauthenticated")


# --- BACKEND-218：POST /api/parent/notifications/{id}/read ----------------------------------------


def _read_url(notification_id: object) -> str:
    return f"{_URL}/{notification_id}/read"


def test_parent_notification_read_success(
    parent_client: ParentClientFactory, db_session: Session
) -> None:
    client, parent = parent_client()
    note = _note(db_session, parent.id)
    db_session.commit()

    resp = client.post(_read_url(note.id))

    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == str(note.id)
    assert body["read_at"] is not None
    assert body["deep_link"] == "/homework"
    assert client.get(_URL).json()["unread_count"] == 0
    # 冪等
    assert client.post(_read_url(note.id)).json()["read_at"] == body["read_at"]


def test_parent_notification_read_422(
    parent_client: ParentClientFactory, assert_error: AssertError
) -> None:
    client, _ = parent_client()

    assert_error(client.post(_read_url("abc")), 422, "validation_error")


def test_parent_notification_read_401(
    api_client: TestClient, staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    assert_error(api_client.post(_read_url(uuid4())), 401, "unauthenticated")
    staff, _ = staff_client(permissions=["students:read"])
    assert_error(staff.post(_read_url(uuid4())), 401, "unauthenticated")


def test_parent_notification_read_idor(
    parent_client: ParentClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    client_a, parent_a = parent_client()
    _, parent_b = parent_client()
    theirs = _note(db_session, parent_b.id, title="B 的通知")
    staff_same_id = _note(db_session, parent_a.id, recipient_type="staff")
    db_session.commit()

    resp = client_a.post(_read_url(theirs.id))
    missing = client_a.post(_read_url(uuid4()))

    assert_error(resp, 404, "notification_not_found")
    assert_error(missing, 404, "notification_not_found")
    assert resp.json() == missing.json()
    assert "B 的通知" not in resp.text
    assert_error(client_a.post(_read_url(staff_same_id.id)), 404, "notification_not_found")
    db_session.expire_all()
    assert theirs.read_at is None


def test_parent_notification_read_foreign_origin(
    parent_client: ParentClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    client, parent = parent_client()
    note = _note(db_session, parent.id)
    db_session.commit()

    resp = client.post(_read_url(note.id), headers={"Origin": "https://evil.test"})

    assert_error(resp, 403, "origin_forbidden")
    db_session.expire_all()
    assert note.read_at is None


# --- BACKEND-221：GET /api/parent/notification-preferences ----------------------------------------

_PREFS = "/api/parent/notification-preferences"


def test_parent_prefs_get_success(parent_client: ParentClientFactory) -> None:
    client, _ = parent_client()

    resp = client.get(_PREFS)

    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 7
    assert all(i["line_enabled"] is True for i in items)
    assert [i["event"] for i in items] == [e.value for e in Event if e in PARENT_LINE_CONFIGURABLE]
    assert set(items[0]) == {"event", "label", "line_enabled"}
    assert set(resp.json()) == {"items"}


def test_parent_prefs_get_401(api_client: TestClient, assert_error: AssertError) -> None:
    assert_error(api_client.get(_PREFS), 401, "unauthenticated")


def test_parent_prefs_get_isolation(
    parent_client: ParentClientFactory, db_session: Session
) -> None:
    client_a, _ = parent_client()
    client_b, parent_b = parent_client()
    db_session.add(
        NotificationPreference(
            parent_account_id=parent_b.id, event="homework.done", line_enabled=False
        )
    )
    db_session.commit()

    a_items = {i["event"]: i["line_enabled"] for i in client_a.get(_PREFS).json()["items"]}
    b_items = {i["event"]: i["line_enabled"] for i in client_b.get(_PREFS).json()["items"]}

    assert a_items["homework.done"] is True
    assert b_items["homework.done"] is False
    assert b_items["exam.published"] is True


def test_parent_prefs_get_staff_cookie(
    staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    staff, _ = staff_client(permissions=["students:read"])

    assert_error(staff.get(_PREFS), 401, "unauthenticated")


def test_parent_prefs_get_labels(parent_client: ParentClientFactory) -> None:
    client, _ = parent_client()

    items = client.get(_PREFS).json()["items"]

    homework = next(i for i in items if i["event"] == "homework.done")
    assert homework["label"] == EVENTS[Event.HOMEWORK_DONE].label == "作業完成"
    assert items[0]["label"] == "到班通知"


# --- BACKEND-222：PUT /api/parent/notification-preferences ----------------------------------------


def _prefs_of(client: TestClient) -> dict[str, bool]:
    return {i["event"]: i["line_enabled"] for i in client.get(_PREFS).json()["items"]}


def test_parent_prefs_put_success(
    parent_client: ParentClientFactory, db_session: Session, fake_clock: FakeClock
) -> None:
    client, parent = parent_client()

    resp = client.put(_PREFS, json={"items": [{"event": "homework.done", "line_enabled": False}]})

    assert resp.status_code == 200
    items = {i["event"]: i["line_enabled"] for i in resp.json()["items"]}
    assert items["homework.done"] is False
    assert items["exam.published"] is True
    assert len(items) == 7
    assert _prefs_of(client)["homework.done"] is False
    # 之後該事件對這位家長不建立 outbox（站內通知仍建立）
    result = enqueue(
        db_session,
        Event.HOMEWORK_DONE,
        recipients=[Recipient("parent", parent.id)],
        payload={"student_id": uuid4(), "student_name": "王小明"},
        clock=fake_clock,
    )
    assert len(result.notification_ids) == 1
    assert result.outbox_ids == []
    # 改回 true 後恢復
    client.put(_PREFS, json={"items": [{"event": "homework.done", "line_enabled": True}]})
    again = enqueue(
        db_session,
        Event.HOMEWORK_DONE,
        recipients=[Recipient("parent", parent.id)],
        payload={"student_id": uuid4(), "student_name": "王小明"},
        clock=fake_clock,
    )
    assert len(again.outbox_ids) == 1


def test_parent_prefs_put_422(
    parent_client: ParentClientFactory, assert_error: AssertError
) -> None:
    client, _ = parent_client()

    empty = client.put(_PREFS, json={"items": []})
    not_configurable = client.put(
        _PREFS, json={"items": [{"event": "binding.completed", "line_enabled": False}]}
    )
    duplicated = client.put(
        _PREFS,
        json={
            "items": [
                {"event": "homework.done", "line_enabled": False},
                {"event": "homework.done", "line_enabled": True},
            ]
        },
    )
    extra = client.put(
        _PREFS, json={"items": [{"event": "homework.done", "line_enabled": False, "x": 1}]}
    )

    assert_error(empty, 422, "validation_error")
    assert_error(not_configurable, 422, "invalid_preference_event")
    assert not_configurable.json()["error"]["details"] == {"events": ["binding.completed"]}
    assert_error(duplicated, 422, "validation_error")
    assert_error(extra, 422, "validation_error")
    assert all(_prefs_of(client).values())


def test_parent_prefs_put_401(
    api_client: TestClient, staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    body = {"items": [{"event": "homework.done", "line_enabled": False}]}

    assert_error(api_client.put(_PREFS, json=body), 401, "unauthenticated")
    staff, _ = staff_client(permissions=["students:read"])
    assert_error(staff.put(_PREFS, json=body), 401, "unauthenticated")


def test_parent_prefs_put_isolation(parent_client: ParentClientFactory) -> None:
    client_a, _ = parent_client()
    client_b, _ = parent_client()

    resp = client_a.put(_PREFS, json={"items": [{"event": "homework.done", "line_enabled": False}]})

    assert resp.status_code == 200
    assert _prefs_of(client_a)["homework.done"] is False
    assert _prefs_of(client_b)["homework.done"] is True


def test_parent_prefs_put_foreign_origin(
    parent_client: ParentClientFactory, assert_error: AssertError
) -> None:
    client, _ = parent_client()

    resp = client.put(
        _PREFS,
        json={"items": [{"event": "homework.done", "line_enabled": False}]},
        headers={"Origin": "https://evil.test"},
    )

    assert_error(resp, 403, "origin_forbidden")
    assert _prefs_of(client)["homework.done"] is True


# --- BACKEND-520：POST /api/parent/notifications/read-all -----------------------------------------

_READ_ALL = f"{_URL}/read-all"


def _unread_count(db: Session, recipient_type: str, recipient_id: UUID) -> int:
    return db.execute(
        select(func.count())
        .select_from(Notification)
        .where(
            Notification.recipient_type == recipient_type,
            Notification.recipient_id == recipient_id,
            Notification.read_at.is_(None),
        )
    ).scalar_one()


def test_parent_notifications_read_all_success(
    parent_client: ParentClientFactory, db_session: Session, fake_clock: FakeClock
) -> None:
    client, parent = parent_client()
    for hour in (7, 8, 9):
        _note(db_session, parent.id, hour=hour)
    already = _note(db_session, parent.id, read=True, hour=6)
    db_session.commit()

    resp = client.post(_READ_ALL)

    assert resp.status_code == 200
    assert resp.json() == {"updated": 3}
    listed = client.get(_URL).json()
    assert listed["unread_count"] == 0
    assert listed["total"] == 4
    db_session.expire_all()
    assert already.read_at == datetime(2026, 9, 1, 12, tzinfo=UTC)  # 已讀的 read_at 不變
    assert _unread_count(db_session, "parent", parent.id) == 0


def test_parent_notifications_read_all_401(
    api_client: TestClient, staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    assert_error(api_client.post(_READ_ALL), 401, "unauthenticated")
    staff, _ = staff_client(permissions=["students:read"])
    assert_error(staff.post(_READ_ALL), 401, "unauthenticated")


def test_parent_notifications_read_all_idor(
    parent_client: ParentClientFactory, db_session: Session
) -> None:
    client_a, parent_a = parent_client()
    _, parent_b = parent_client()
    staff = make_staff(db_session)
    _note(db_session, parent_a.id)
    _note(db_session, parent_b.id, title="B 1")
    _note(db_session, parent_b.id, title="B 2", hour=9)
    _note(db_session, staff.id, title="員工", recipient_type="staff")
    # 與家長 A 同 id 的員工通知也不受影響
    _note(db_session, parent_a.id, title="同 id 員工", recipient_type="staff", hour=10)
    db_session.commit()

    resp = client_a.post(_READ_ALL)

    assert resp.json() == {"updated": 1}
    db_session.expire_all()
    assert _unread_count(db_session, "parent", parent_b.id) == 2
    assert _unread_count(db_session, "staff", staff.id) == 1
    assert _unread_count(db_session, "staff", parent_a.id) == 1


def test_parent_notifications_read_all_none(parent_client: ParentClientFactory) -> None:
    client, _ = parent_client()

    resp = client.post(_READ_ALL)

    assert resp.status_code == 200
    assert resp.json() == {"updated": 0}


def test_parent_notifications_read_all_foreign_origin(
    parent_client: ParentClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    client, parent = parent_client()
    note = _note(db_session, parent.id)
    db_session.commit()

    resp = client.post(_READ_ALL, headers={"Origin": "https://evil.test"})

    assert_error(resp, 403, "origin_forbidden")
    db_session.expire_all()
    assert note.read_at is None
