"""BACKEND-206：app/notifications/service.py（NotificationService.enqueue）。

after-commit 以 db_session 的 commit 觸發（savepoint 模式的 commit 仍會發 after_commit，見
BACKEND-006 tx_hooks 的實測結論）；kick_outbox 以 monkeypatch 記錄，ws 推播則記錄 BACKEND-224 底層
的 publish_threadsafe（push_to_recipient_after_commit 本身負責 after-commit 排程，不能被換掉）。
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.tx_hooks import install_tx_hooks
from app.models.notifications import Notification, NotificationOutbox, NotificationPreference
from app.notifications import service as service_module
from app.notifications.events import Event
from app.notifications.recipients import Recipient
from app.notifications.service import EnqueueResult, enqueue
from app.realtime import publish as publish_module
from app.realtime.publish import parent_channel, staff_channel
from app.services.settings_service import invalidate_setting
from tests.support.factories import make_parent, make_staff
from tests.support.fake_clock import FakeClock

PushCall = tuple[list[str], dict[str, Any]]


@pytest.fixture(autouse=True)
def _hooks() -> None:
    install_tx_hooks()


@pytest.fixture
def kicked(monkeypatch: pytest.MonkeyPatch) -> list[list[UUID]]:
    calls: list[list[UUID]] = []
    monkeypatch.setattr(service_module, "kick_outbox", lambda ids: calls.append(list(ids)))
    return calls


@pytest.fixture
def pushed(monkeypatch: pytest.MonkeyPatch) -> list[PushCall]:
    """記錄 commit 後實際送出的 (channels, envelope)。"""
    calls: list[PushCall] = []

    def record(channels: list[str], message: dict[str, Any]) -> None:
        calls.append((list(channels), dict(message)))

    monkeypatch.setattr(publish_module, "publish_threadsafe", record)
    return calls


@pytest.fixture
def toggles_off(db_session: Session) -> Iterator[None]:
    toggles = {e.value: True for e in Event}
    toggles[Event.HOMEWORK_DONE.value] = False
    db_session.execute(
        text(
            "update public.system_settings set value = cast(:v as jsonb) "
            "where key = 'notification.toggles'"
        ),
        {"v": json.dumps(toggles)},
    )
    invalidate_setting("notification.toggles")
    yield
    invalidate_setting("notification.toggles")


def _count(db_session: Session, model: type[Any]) -> int:
    return int(db_session.execute(select(func.count()).select_from(model)).scalar_one())


def _notifications(db_session: Session, ids: list[UUID]) -> list[Notification]:
    return list(
        db_session.execute(
            select(Notification).where(Notification.id.in_(ids)).order_by(Notification.created_at)
        ).scalars()
    )


def _homework_payload(student_id: UUID) -> dict[str, Any]:
    return {"student_id": student_id, "student_name": "王小明"}


def test_enqueue_in_app_and_line(
    db_session: Session, fake_clock: FakeClock, kicked: list[list[UUID]], pushed: list[PushCall]
) -> None:
    p1 = make_parent(db_session, display_name="王媽媽")
    p2 = make_parent(db_session, display_name="王爸爸")
    db_session.add(
        NotificationPreference(
            parent_account_id=p2.id, event=Event.HOMEWORK_DONE.value, line_enabled=False
        )
    )
    # 別的事件關閉不影響 homework.done
    db_session.add(
        NotificationPreference(
            parent_account_id=p1.id, event=Event.EXAM_PUBLISHED.value, line_enabled=False
        )
    )
    db_session.flush()
    student_id = uuid4()

    result = enqueue(
        db_session,
        Event.HOMEWORK_DONE,
        recipients=[Recipient("parent", p1.id), Recipient("parent", p2.id)],
        payload=_homework_payload(student_id),
        clock=fake_clock,
    )

    assert isinstance(result, EnqueueResult)
    assert len(result.notification_ids) == 2
    assert len(result.outbox_ids) == 1
    rows = _notifications(db_session, result.notification_ids)
    assert {r.recipient_id for r in rows} == {p1.id, p2.id}
    assert all(r.recipient_type == "parent" for r in rows)
    assert all(r.event == "homework.done" for r in rows)
    assert all(r.title == "王小明 作業已完成" for r in rows)
    assert all(r.read_at is None for r in rows)
    # payload 以 JSON 相容形式存入（UUID → str）
    assert all(r.payload == {"student_id": str(student_id), "student_name": "王小明"} for r in rows)
    outbox = db_session.execute(
        select(NotificationOutbox).where(NotificationOutbox.id == result.outbox_ids[0])
    ).scalar_one()
    assert outbox.notification.recipient_id == p1.id
    assert outbox.channel == "line"
    assert outbox.status == "pending"
    assert outbox.attempts == 0
    assert outbox.next_attempt_at == fake_clock.now()
    # 尚未 commit：不 kick、不推播
    assert kicked == []
    assert pushed == []

    db_session.commit()

    assert kicked == [result.outbox_ids]
    assert len(pushed) == 2
    assert {tuple(c[0]) for c in pushed} == {
        (parent_channel(p1.id),),
        (parent_channel(p2.id),),
    }
    assert all(c[1]["type"] == "notification.created" for c in pushed)
    data = next(c[1]["data"] for c in pushed if c[0] == [parent_channel(p1.id)])
    assert data["title"] == "王小明 作業已完成"
    assert data["event"] == "homework.done"
    assert data["deep_link"] == "/homework"
    assert data["read_at"] is None
    assert data["id"] in {str(i) for i in result.notification_ids}
    assert set(data) == {
        "id",
        "event",
        "title",
        "body",
        "payload",
        "read_at",
        "created_at",
        "deep_link",
    }


def test_enqueue_ws_only_event(
    db_session: Session, fake_clock: FakeClock, kicked: list[list[UUID]], pushed: list[PushCall]
) -> None:
    staff = make_staff(db_session, role_code="tutor")
    before_n, before_o = _count(db_session, Notification), _count(db_session, NotificationOutbox)
    student_id = uuid4()
    request_id = uuid4()

    result = enqueue(
        db_session,
        Event.PICKUP_ARRIVED,
        recipients=[Recipient("staff", staff.id)],
        payload={"student_id": student_id, "student_name": "王小明", "request_id": request_id},
        clock=fake_clock,
    )
    db_session.commit()

    assert result == EnqueueResult(notification_ids=[], outbox_ids=[])
    assert _count(db_session, Notification) == before_n
    assert _count(db_session, NotificationOutbox) == before_o
    assert kicked == []
    assert len(pushed) == 1
    channels, message = pushed[0]
    assert channels == [staff_channel(staff.id)]
    assert message["type"] == "notification.transient"
    data = message["data"]
    assert data["event"] == "pickup.arrived"
    assert "王小明" in data["title"]
    assert data["payload"] == {
        "student_id": str(student_id),
        "student_name": "王小明",
        "request_id": str(request_id),
    }
    assert set(data) == {"event", "title", "body", "payload"}


@pytest.mark.usefixtures("toggles_off")
def test_enqueue_toggle_off(
    db_session: Session, fake_clock: FakeClock, kicked: list[list[UUID]], pushed: list[PushCall]
) -> None:
    p1 = make_parent(db_session)
    before = _count(db_session, Notification)

    result = enqueue(
        db_session,
        Event.HOMEWORK_DONE,
        recipients=[Recipient("parent", p1.id)],
        payload=_homework_payload(uuid4()),
        clock=fake_clock,
    )
    db_session.commit()

    assert result == EnqueueResult(notification_ids=[], outbox_ids=[])
    assert _count(db_session, Notification) == before
    assert kicked == []
    assert pushed == []
    # 其他事件仍開啟
    other = enqueue(
        db_session,
        Event.BINDING_COMPLETED,
        recipients=[Recipient("parent", p1.id)],
        payload=_homework_payload(uuid4()),
        clock=fake_clock,
    )
    assert len(other.notification_ids) == 1


def test_enqueue_validation(db_session: Session, fake_clock: FakeClock) -> None:
    staff = make_staff(db_session, role_code="tutor")
    p1 = make_parent(db_session)
    before = _count(db_session, Notification)

    with pytest.raises(ValueError, match="staff"):
        enqueue(
            db_session,
            Event.HOMEWORK_DONE,
            recipients=[Recipient("staff", staff.id)],
            payload=_homework_payload(uuid4()),
            clock=fake_clock,
        )
    with pytest.raises(ValueError, match="student_name"):
        enqueue(
            db_session,
            Event.HOMEWORK_DONE,
            recipients=[Recipient("parent", p1.id)],
            payload={"student_id": uuid4()},
            clock=fake_clock,
        )

    assert _count(db_session, Notification) == before
    # 空收件人清單：回空結果、不寫入
    empty = enqueue(
        db_session,
        Event.HOMEWORK_DONE,
        recipients=[],
        payload=_homework_payload(uuid4()),
        clock=fake_clock,
    )
    assert empty == EnqueueResult(notification_ids=[], outbox_ids=[])


def test_enqueue_after_commit_only(
    db_session: Session, fake_clock: FakeClock, kicked: list[list[UUID]], pushed: list[PushCall]
) -> None:
    p1 = make_parent(db_session)
    db_session.commit()

    result = enqueue(
        db_session,
        Event.HOMEWORK_DONE,
        recipients=[Recipient("parent", p1.id)],
        payload=_homework_payload(uuid4()),
        clock=fake_clock,
    )
    assert kicked == []
    assert pushed == []

    db_session.commit()
    assert kicked == [result.outbox_ids]
    assert len(pushed) == 1

    # rollback：什麼都不發生，列也不存在
    rolled_back = enqueue(
        db_session,
        Event.HOMEWORK_DONE,
        recipients=[Recipient("parent", p1.id)],
        payload=_homework_payload(uuid4()),
        clock=fake_clock,
    )
    db_session.rollback()
    assert kicked == [result.outbox_ids]
    assert len(pushed) == 1
    assert _notifications(db_session, rolled_back.notification_ids) == []
    db_session.commit()
    assert len(kicked) == 1


def test_enqueue_dedupe_recipients(db_session: Session, fake_clock: FakeClock) -> None:
    p1 = make_parent(db_session)

    result = enqueue(
        db_session,
        Event.HOMEWORK_DONE,
        recipients=[Recipient("parent", p1.id), Recipient("parent", p1.id)],
        payload=_homework_payload(uuid4()),
        clock=fake_clock,
    )

    assert len(result.notification_ids) == 1
    assert len(result.outbox_ids) == 1
    assert len(_notifications(db_session, result.notification_ids)) == 1


def test_enqueue_mixed_recipient_event(db_session: Session, fake_clock: FakeClock) -> None:
    p1 = make_parent(db_session)
    s1 = make_staff(db_session, role_code="tutor")
    payload = {
        "student_id": uuid4(),
        "student_name": "王小明",
        "request_id": uuid4(),
        "cancelled_by_label": "老師",
    }

    to_parent = enqueue(
        db_session,
        Event.PICKUP_CANCELLED,
        recipients=[Recipient("parent", p1.id)],
        payload=payload,
        clock=fake_clock,
    )
    to_staff = enqueue(
        db_session,
        Event.PICKUP_CANCELLED,
        recipients=[Recipient("staff", s1.id)],
        payload=payload,
        clock=fake_clock,
    )

    assert (len(to_parent.notification_ids), len(to_parent.outbox_ids)) == (1, 1)
    assert (len(to_staff.notification_ids), len(to_staff.outbox_ids)) == (1, 0)
    staff_row = _notifications(db_session, to_staff.notification_ids)[0]
    assert (staff_row.recipient_type, staff_row.recipient_id) == ("staff", s1.id)
    assert staff_row.payload["cancelled_by_label"] == "老師"


# --- BACKEND-545：savepoint 回滾後不推幽靈通知 ---------------------------------------------------


def test_enqueue_inside_rolled_back_savepoint_no_ghost(
    db_session: Session, fake_clock: FakeClock, kicked: list[list[UUID]], pushed: list[PushCall]
) -> None:
    p1 = make_parent(db_session)
    db_session.commit()

    result: EnqueueResult | None = None
    with contextlib.suppress(RuntimeError), db_session.begin_nested():
        result = enqueue(
            db_session,
            Event.HOMEWORK_DONE,
            recipients=[Recipient("parent", p1.id)],
            payload=_homework_payload(uuid4()),
            clock=fake_clock,
        )
        raise RuntimeError("業務錯誤，savepoint 回滾")
    db_session.commit()

    assert result is not None
    assert len(result.outbox_ids) == 1
    assert kicked == []
    assert pushed == []
    assert _notifications(db_session, result.notification_ids) == []
    # savepoint 外的 enqueue 不受影響
    kept = enqueue(
        db_session,
        Event.HOMEWORK_DONE,
        recipients=[Recipient("parent", p1.id)],
        payload=_homework_payload(uuid4()),
        clock=fake_clock,
    )
    db_session.commit()
    assert kicked == [kept.outbox_ids]
    assert len(pushed) == 1
