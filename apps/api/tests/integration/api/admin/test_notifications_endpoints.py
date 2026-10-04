"""BACKEND-214：GET /api/admin/notifications（個人收件匣，只需登入）。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.account import StaffUser
from app.models.notifications import Notification
from tests.support.route_audit import admin_routes_without_permission

_URL = "/api/admin/notifications"
StaffClientFactory = Callable[..., tuple[TestClient, StaffUser]]
AssertError = Callable[..., None]


def _note(
    db: Session,
    recipient_id: UUID,
    *,
    title: str = "新的請假申請",
    event: str = "leave.created",
    read: bool = False,
    hour: int = 8,
    recipient_type: str = "staff",
) -> Notification:
    row = Notification(
        recipient_type=recipient_type,
        recipient_id=recipient_id,
        event=event,
        title=title,
        body="王小明 申請請假",
        payload={"student_id": str(uuid4())},
        read_at=datetime(2026, 9, 1, 12, tzinfo=UTC) if read else None,
        created_at=datetime(2026, 9, 1, hour, tzinfo=UTC),
    )
    db.add(row)
    db.flush()
    return row


def test_admin_notifications_list_success(
    staff_client: StaffClientFactory, db_session: Session
) -> None:
    client, staff = staff_client(permissions=[])
    _note(db_session, staff.id, hour=8)
    _note(db_session, staff.id, hour=9)
    db_session.commit()

    resp = client.get(_URL)

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert body["unread_count"] == 2
    assert "請假" in body["items"][0]["title"]
    assert body["items"][0]["deep_link"] == "/leaves"
    assert set(body["items"][0]) == {
        "id",
        "event",
        "title",
        "body",
        "payload",
        "read_at",
        "created_at",
        "deep_link",
    }


def test_admin_notifications_list_unread_only_and_paging(
    staff_client: StaffClientFactory, db_session: Session
) -> None:
    client, staff = staff_client(permissions=[])
    _note(db_session, staff.id, title="已讀", read=True, hour=7)
    for hour in (8, 9, 10):
        _note(db_session, staff.id, title=f"未讀{hour}", hour=hour)
    db_session.commit()

    unread = client.get(_URL, params={"unread_only": "true"}).json()
    page2 = client.get(_URL, params={"page": 2, "page_size": 2}).json()

    assert unread["total"] == 3
    assert [i["title"] for i in unread["items"]] == ["未讀10", "未讀9", "未讀8"]
    assert page2["total"] == 4
    assert [i["title"] for i in page2["items"]] == ["未讀8", "已讀"]
    assert page2["unread_count"] == 3


def test_admin_notifications_list_422(
    staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    client, _ = staff_client(permissions=[])

    for params in ({"page_size": 0}, {"page": 0}, {"unread_only": "maybe"}, {"foo": "bar"}):
        assert_error(client.get(_URL, params=params), 422, "validation_error")


def test_admin_notifications_list_401(api_client: TestClient, assert_error: AssertError) -> None:
    assert_error(api_client.get(_URL), 401, "unauthenticated")


def test_admin_notifications_list_403_must_change_password(
    staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    client, _ = staff_client(permissions=[], must_change_password=True)

    assert_error(client.get(_URL), 403, "password_change_required")


def test_admin_notifications_list_isolation(
    staff_client: StaffClientFactory, db_session: Session
) -> None:
    client, staff = staff_client(permissions=[])
    other = uuid4()
    _note(db_session, staff.id, title="我的")
    _note(db_session, other, title="別人的")
    _note(db_session, staff.id, title="同 id 的家長通知", recipient_type="parent")
    db_session.commit()

    resp = client.get(_URL)

    assert [i["title"] for i in resp.json()["items"]] == ["我的"]
    assert "別人的" not in resp.text
    assert "同 id 的家長通知" not in resp.text


def test_admin_notifications_list_requires_no_permission_code(
    staff_client: StaffClientFactory, app: FastAPI
) -> None:
    client, _ = staff_client(permissions=[])

    assert client.get(_URL).status_code == 200
    # 個人收件匣在 route audit 白名單內，其餘後台路由仍須掛權限守衛
    assert admin_routes_without_permission(app) == []
    assert "/api/admin/notifications" in app.openapi()["paths"]
