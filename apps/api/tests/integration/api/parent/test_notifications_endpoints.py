"""BACKEND-217：GET /api/parent/notifications。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.account import StaffUser
from app.models.notifications import Notification
from app.models.parents import ParentAccount

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
