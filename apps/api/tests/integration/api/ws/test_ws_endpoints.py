"""BACKEND-226：/api/ws/admin（員工 WebSocket）。
BACKEND-227：/api/ws/parent（家長 WebSocket）。

WS handler 以 ``open_session``（獨立 SessionLocal）讀 DB，看不到 db_session 未提交的資料：測資以綁
``db_engine`` 的真 commit session 建立，teardown 以 owner 連線逐筆刪除。「收不到」以哨兵訊息驗證：
先 publish 到不該收到的頻道，再 publish 哨兵到已訂閱的個人頻道，下一則收到的必須是哨兵（broadcaster
依排程順序送出）。
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from uuid import UUID

import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session
from starlette.websockets import WebSocketDisconnect

from app.api.ws import admin as ws_admin
from app.api.ws import parent as ws_parent
from app.core import db as core_db
from app.core.security.cookies import PARENT_ACCESS, STAFF_ACCESS
from app.core.security.tokens import create_access_token
from app.models.account import StaffUser
from app.models.parents import ParentAccount
from app.realtime.broadcaster import publish_threadsafe
from app.realtime.publish import (
    admin_topic_channel,
    broadcast_after_commit,
    parent_channel,
    staff_channel,
    student_channel,
)
from tests.integration.db.conftest import connect_owner
from tests.support.factories import (
    ARCHIVED_AT,
    make_guardian,
    make_parent,
    make_staff,
    make_student,
)
from tests.support.fake_clock import FakeClock

_ADMIN = "/api/ws/admin"
_PARENT = "/api/ws/parent"
_SENTINEL: dict[str, Any] = {"type": "test.sentinel", "data": {"n": 1}}


@pytest.fixture(autouse=True)
def _ws_engine(monkeypatch: pytest.MonkeyPatch, db_engine: Engine) -> None:
    """WS handler 的 open_session 用測試 engine（app_backend）。"""
    monkeypatch.setattr(core_db, "get_engine", lambda: db_engine)


@pytest.fixture
def owner_cleanup() -> Iterator[list[tuple[str, UUID]]]:
    """(table, id) 清單；teardown 以 owner 連線反序逐筆刪除（後建的子列先刪）。只建 seed 系統角色的
    員工，不往 seed 表寫列。"""
    rows: list[tuple[str, UUID]] = []
    yield rows
    with connect_owner() as conn:
        conn.execute("set lock_timeout = '5s'")
        for table, row_id in reversed(rows):
            conn.execute(
                psycopg.sql.SQL("delete from public.{} where id = %s").format(
                    psycopg.sql.Identifier(table)
                ),
                (row_id,),
            )
        conn.commit()


@pytest.fixture
def committed(owner_cleanup: list[tuple[str, UUID]], db_engine: Engine) -> Iterator[Session]:
    """真 commit 的 session（WS handler 另開連線也看得到）；參數順序讓 session 先於 cleanup
    關閉。"""
    session = Session(bind=db_engine, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()


def _staff_cookie(staff: StaffUser, clock: FakeClock) -> dict[str, str]:
    token = create_access_token(
        subject_type="staff", subject_id=staff.id, token_version=staff.token_version, clock=clock
    )
    return {"Cookie": f"{STAFF_ACCESS.name}={token}"}


def _parent_cookie(parent: ParentAccount, clock: FakeClock) -> dict[str, str]:
    token = create_access_token(
        subject_type="parent",
        subject_id=parent.id,
        token_version=parent.token_version,
        clock=clock,
    )
    return {"Cookie": f"{PARENT_ACCESS.name}={token}"}


def _close_code(client: TestClient, path: str, headers: dict[str, str]) -> int:
    with (
        pytest.raises(WebSocketDisconnect) as excinfo,
        client.websocket_connect(path, headers=headers),
    ):
        pass
    return excinfo.value.code


def _committed_staff(session: Session, cleanup: list[tuple[str, UUID]], **kwargs: Any) -> StaffUser:
    staff = make_staff(session, role_code=kwargs.pop("role_code", "tutor"), **kwargs)
    session.commit()
    cleanup.append(("staff_users", staff.id))
    return staff


def _committed_family(
    session: Session, cleanup: list[tuple[str, UUID]]
) -> tuple[ParentAccount, Any, Any, Any]:
    """王媽媽綁王小明；陳小華是別人的小孩（陳媽媽）。
    回 (王媽媽, 王小明, 陳小華, 王小明的 guardian)。"""
    parent = make_parent(session, display_name="王媽媽")
    other = make_parent(session, display_name="陳媽媽")
    ming = make_student(session, name="王小明")
    hua = make_student(session, name="陳小華")
    g_ming = make_guardian(session, ming, parent=parent)
    g_hua = make_guardian(session, hua, parent=other, name="陳媽媽")
    session.commit()
    cleanup.extend(
        [
            ("parent_accounts", parent.id),
            ("parent_accounts", other.id),
            ("students", ming.id),
            ("students", hua.id),
            ("guardians", g_ming.id),
            ("guardians", g_hua.id),
        ]
    )
    return parent, ming, hua, g_ming


# --- BACKEND-226：/api/ws/admin -------------------------------------------------------------------


def test_ws_admin_ready_and_notification(
    api_client: TestClient,
    committed: Session,
    owner_cleanup: list[tuple[str, UUID]],
    fake_clock: FakeClock,
) -> None:
    staff = _committed_staff(committed, owner_cleanup)

    with api_client.websocket_connect(_ADMIN, headers=_staff_cookie(staff, fake_clock)) as ws:
        assert ws.receive_json() == {"type": "ready", "topics": []}

        message = {
            "type": "notification.created",
            "data": {"id": "n1", "title": "王小明 作業已完成"},
        }
        publish_threadsafe([staff_channel(staff.id)], message)
        assert ws.receive_json() == message

        # 別人的個人頻道收不到
        publish_threadsafe([staff_channel(UUID(int=9))], {"type": "notification.created"})
        publish_threadsafe([staff_channel(staff.id)], _SENTINEL)
        assert ws.receive_json() == _SENTINEL
        # ping / pong 仍可用
        ws.send_json({"action": "ping"})
        assert ws.receive_json() == {"type": "pong"}


def test_ws_admin_subscribe_with_permission(
    api_client: TestClient,
    committed: Session,
    owner_cleanup: list[tuple[str, UUID]],
    fake_clock: FakeClock,
) -> None:
    staff = _committed_staff(committed, owner_cleanup)  # tutor 有 pickup:read

    with api_client.websocket_connect(_ADMIN, headers=_staff_cookie(staff, fake_clock)) as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"action": "subscribe", "topics": ["pickup"]})
        assert ws.receive_json() == {"type": "subscribed", "topics": ["pickup"]}

        message = {"type": "pickup.request_updated", "data": {"id": "r1"}, "sent_at": "x"}
        publish_threadsafe([admin_topic_channel("pickup")], message)
        assert ws.receive_json() == message

        # 再訂 attendance：回目前全部（排序）；重複訂閱不重複收
        ws.send_json({"action": "subscribe", "topics": ["attendance", "pickup"]})
        assert ws.receive_json() == {"type": "subscribed", "topics": ["attendance", "pickup"]}
        publish_threadsafe([admin_topic_channel("pickup")], message)
        assert ws.receive_json() == message
        publish_threadsafe([staff_channel(staff.id)], _SENTINEL)
        assert ws.receive_json() == _SENTINEL

        # 取消後收不到
        ws.send_json({"action": "unsubscribe", "topics": ["pickup"]})
        assert ws.receive_json() == {"type": "subscribed", "topics": ["attendance"]}
        publish_threadsafe([admin_topic_channel("pickup")], message)
        publish_threadsafe([staff_channel(staff.id)], _SENTINEL)
        assert ws.receive_json() == _SENTINEL


def test_ws_admin_subscribe_forbidden(
    api_client: TestClient,
    committed: Session,
    owner_cleanup: list[tuple[str, UUID]],
    fake_clock: FakeClock,
) -> None:
    staff = _committed_staff(committed, owner_cleanup, revoked_permissions=["homework:read"])

    with api_client.websocket_connect(_ADMIN, headers=_staff_cookie(staff, fake_clock)) as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"action": "subscribe", "topics": ["homework"]})
        assert ws.receive_json() == {
            "type": "error",
            "code": "permission_denied",
            "topics": ["homework"],
        }

        publish_threadsafe([admin_topic_channel("homework")], {"type": "homework.progress_updated"})
        publish_threadsafe([staff_channel(staff.id)], _SENTINEL)
        assert ws.receive_json() == _SENTINEL

        # 混合：有權限的訂上、沒權限的回 error；未知 topic / 格式錯 → bad_message
        ws.send_json({"action": "subscribe", "topics": ["homework", "pickup"]})
        assert ws.receive_json() == {
            "type": "error",
            "code": "permission_denied",
            "topics": ["homework"],
        }
        assert ws.receive_json() == {"type": "subscribed", "topics": ["pickup"]}
        ws.send_json({"action": "subscribe", "topics": ["weather"]})
        assert ws.receive_json() == {"type": "error", "code": "bad_message"}
        ws.send_json({"action": "subscribe", "topics": "pickup"})
        assert ws.receive_json() == {"type": "error", "code": "bad_message"}
        ws.send_json({"action": "dance"})
        assert ws.receive_json() == {"type": "error", "code": "bad_message"}


def test_ws_admin_unauthenticated(
    api_client: TestClient,
    committed: Session,
    owner_cleanup: list[tuple[str, UUID]],
    fake_clock: FakeClock,
) -> None:
    parent = make_parent(committed)
    committed.commit()
    owner_cleanup.append(("parent_accounts", parent.id))

    assert _close_code(api_client, _ADMIN, {}) == 4401
    assert _close_code(api_client, _ADMIN, _parent_cookie(parent, fake_clock)) == 4401
    assert _close_code(api_client, _ADMIN, {"Cookie": f"{STAFF_ACCESS.name}=garbage"}) == 4401


def test_ws_admin_foreign_origin(
    api_client: TestClient,
    committed: Session,
    owner_cleanup: list[tuple[str, UUID]],
    fake_clock: FakeClock,
) -> None:
    staff = _committed_staff(committed, owner_cleanup)
    headers = {**_staff_cookie(staff, fake_clock), "Origin": "https://evil.test"}

    assert _close_code(api_client, _ADMIN, headers) == 4403


def test_ws_admin_revalidate_revoked(
    api_client: TestClient,
    committed: Session,
    owner_cleanup: list[tuple[str, UUID]],
    fake_clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ws_admin, "REVALIDATE_SECONDS", 0.1)
    staff = _committed_staff(committed, owner_cleanup)

    with api_client.websocket_connect(_ADMIN, headers=_staff_cookie(staff, fake_clock)) as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"action": "subscribe", "topics": ["pickup", "attendance"]})
        assert ws.receive_json() == {"type": "subscribed", "topics": ["attendance", "pickup"]}

        staff.revoked_permissions = ["pickup:read"]
        committed.commit()

        assert ws.receive_json() == {
            "type": "unsubscribed",
            "topics": ["pickup"],
            "reason": "permission_revoked",
        }
        publish_threadsafe([admin_topic_channel("pickup")], {"type": "pickup.request_updated"})
        publish_threadsafe([staff_channel(staff.id)], _SENTINEL)
        assert ws.receive_json() == _SENTINEL

        staff.is_active = False
        committed.commit()

        with pytest.raises(WebSocketDisconnect) as excinfo:
            ws.receive_json()
        assert excinfo.value.code == 4401


# --- BACKEND-227：/api/ws/parent ------------------------------------------------------------------


def test_ws_parent_ready_children(
    api_client: TestClient,
    committed: Session,
    owner_cleanup: list[tuple[str, UUID]],
    fake_clock: FakeClock,
) -> None:
    parent, ming, _, _ = _committed_family(committed, owner_cleanup)

    with api_client.websocket_connect(_PARENT, headers=_parent_cookie(parent, fake_clock)) as ws:
        assert ws.receive_json() == {"type": "ready", "children": [str(ming.id)]}
        ws.send_json({"action": "ping"})
        assert ws.receive_json() == {"type": "pong"}


def test_ws_parent_receives_own_child_only(
    api_client: TestClient,
    committed: Session,
    owner_cleanup: list[tuple[str, UUID]],
    fake_clock: FakeClock,
) -> None:
    parent, ming, hua, _ = _committed_family(committed, owner_cleanup)

    with api_client.websocket_connect(_PARENT, headers=_parent_cookie(parent, fake_clock)) as ws:
        assert ws.receive_json()["type"] == "ready"

        own = {"type": "homework.progress_updated", "data": {"student_id": str(ming.id)}}
        publish_threadsafe([student_channel(ming.id)], own)
        assert ws.receive_json() == own

        personal = {"type": "notification.created", "data": {"id": "n1"}}
        publish_threadsafe([parent_channel(parent.id)], personal)
        assert ws.receive_json() == personal

        # 別人小孩的 student channel 收不到（IDOR）
        publish_threadsafe(
            [student_channel(hua.id)],
            {"type": "homework.progress_updated", "data": {"student_id": str(hua.id)}},
        )
        publish_threadsafe([parent_channel(parent.id)], _SENTINEL)
        assert ws.receive_json() == _SENTINEL


def test_ws_parent_cannot_subscribe(
    api_client: TestClient,
    committed: Session,
    owner_cleanup: list[tuple[str, UUID]],
    fake_clock: FakeClock,
) -> None:
    parent, _, _, _ = _committed_family(committed, owner_cleanup)

    with api_client.websocket_connect(_PARENT, headers=_parent_cookie(parent, fake_clock)) as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"action": "subscribe", "topics": ["pickup"]})
        assert ws.receive_json() == {"type": "error", "code": "not_allowed"}
        ws.send_json({"action": "unsubscribe", "topics": ["pickup"]})
        assert ws.receive_json() == {"type": "error", "code": "not_allowed"}

        publish_threadsafe([admin_topic_channel("pickup")], {"type": "pickup.request_updated"})
        publish_threadsafe([parent_channel(parent.id)], _SENTINEL)
        assert ws.receive_json() == _SENTINEL


def test_ws_parent_unbind_revalidate(
    api_client: TestClient,
    committed: Session,
    owner_cleanup: list[tuple[str, UUID]],
    fake_clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ws_parent, "REVALIDATE_SECONDS", 0.1)
    parent, ming, hua, g_ming = _committed_family(committed, owner_cleanup)

    with api_client.websocket_connect(_PARENT, headers=_parent_cookie(parent, fake_clock)) as ws:
        assert ws.receive_json() == {"type": "ready", "children": [str(ming.id)]}

        g_ming.archived_at = ARCHIVED_AT
        committed.commit()

        assert ws.receive_json() == {"type": "children_changed", "children": []}
        publish_threadsafe([student_channel(ming.id)], {"type": "homework.progress_updated"})
        publish_threadsafe([parent_channel(parent.id)], _SENTINEL)
        assert ws.receive_json() == _SENTINEL

        # 新綁定的學生加入訂閱
        g_hua = make_guardian(committed, hua, parent=parent, name="王媽媽")
        committed.commit()
        owner_cleanup.append(("guardians", g_hua.id))
        assert ws.receive_json() == {"type": "children_changed", "children": [str(hua.id)]}
        message = {"type": "homework.progress_updated", "data": {"student_id": str(hua.id)}}
        publish_threadsafe([student_channel(hua.id)], message)
        assert ws.receive_json() == message


def test_ws_parent_unauthenticated(
    api_client: TestClient,
    committed: Session,
    owner_cleanup: list[tuple[str, UUID]],
    fake_clock: FakeClock,
) -> None:
    staff = _committed_staff(committed, owner_cleanup)
    parent, _, _, _ = _committed_family(committed, owner_cleanup)

    assert _close_code(api_client, _PARENT, {}) == 4401
    assert _close_code(api_client, _PARENT, _staff_cookie(staff, fake_clock)) == 4401
    headers = {**_parent_cookie(parent, fake_clock), "Origin": "https://evil.test"}
    assert _close_code(api_client, _PARENT, headers) == 4403


def test_ws_parent_receives_attendance(
    api_client: TestClient,
    committed: Session,
    owner_cleanup: list[tuple[str, UUID]],
    fake_clock: FakeClock,
) -> None:
    """出勤 service（BACKEND-310 / 305）以 broadcast_after_commit 送 student channel 的家長裁切版；
    這裡直接驅動 publish.py 的同一條路徑，驗證 endpoint 原樣轉送。"""
    parent, ming, hua, _ = _committed_family(committed, owner_cleanup)
    parent_data = {
        "student_id": str(ming.id),
        "service_date": "2026-09-01",
        "status": "present",
        "check_in_at": "2026-09-01T07:00:00Z",
        "check_out_at": None,
    }

    with api_client.websocket_connect(_PARENT, headers=_parent_cookie(parent, fake_clock)) as ws:
        assert ws.receive_json()["type"] == "ready"

        broadcast_after_commit(
            committed,
            topic="attendance",
            type="attendance.updated",
            data={**parent_data, "staff_name": "林老師", "note": "內部備註"},
            clock=fake_clock,
            student_id=ming.id,
            parent_data=parent_data,
        )
        committed.commit()

        received = ws.receive_json()
        assert received["type"] == "attendance.updated"
        assert received["data"] == parent_data
        assert "林老師" not in str(received)

        # 他人小孩的出勤事件收不到
        broadcast_after_commit(
            committed,
            topic="attendance",
            type="attendance.updated",
            data={**parent_data, "student_id": str(hua.id)},
            clock=fake_clock,
            student_id=hua.id,
            parent_data={**parent_data, "student_id": str(hua.id)},
        )
        committed.commit()
        publish_threadsafe([parent_channel(parent.id)], _SENTINEL)
        assert ws.receive_json() == _SENTINEL
