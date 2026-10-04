"""BACKEND-224：app/realtime/publish.py（頻道命名、訊息信封、after-commit 廣播 helper）。

以 monkeypatch 記錄 publish_threadsafe 的呼叫；commit 走 db_session（savepoint 模式的 commit 會
觸發 after_commit，見 BACKEND-006）。
"""

from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from app.core.tx_hooks import install_tx_hooks
from app.notifications.recipients import Recipient
from app.realtime import publish as publish_module
from app.realtime.publish import (
    PARENT_VISIBLE_TOPICS,
    admin_topic_channel,
    broadcast_after_commit,
    envelope,
    parent_channel,
    push_to_recipient_after_commit,
    push_to_student_after_commit,
    staff_channel,
    student_channel,
)
from tests.support.fake_clock import FakeClock

Call = tuple[list[str], dict[str, Any]]


@pytest.fixture
def published(monkeypatch: pytest.MonkeyPatch) -> list[Call]:
    install_tx_hooks()
    calls: list[Call] = []

    def record(channels: list[str], message: dict[str, Any]) -> None:
        calls.append((list(channels), dict(message)))

    monkeypatch.setattr(publish_module, "publish_threadsafe", record)
    return calls


def test_publish_channel_names() -> None:
    assert admin_topic_channel("pickup") == "admin:pickup"
    assert admin_topic_channel("homework") == "admin:homework"
    assert staff_channel(UUID(int=3)) == "staff:00000000-0000-0000-0000-000000000003"
    assert parent_channel(UUID(int=4)) == "parent:00000000-0000-0000-0000-000000000004"
    assert student_channel(UUID(int=1)) == "student:00000000-0000-0000-0000-000000000001"
    assert frozenset({"pickup", "homework", "attendance"}) == PARENT_VISIBLE_TOPICS


def test_publish_envelope(fake_clock: FakeClock) -> None:
    assert envelope("pickup.request_updated", {"id": UUID(int=2)}, clock=fake_clock) == {
        "type": "pickup.request_updated",
        "data": {"id": "00000000-0000-0000-0000-000000000002"},
        "sent_at": "2026-09-01T01:00:00Z",
    }
    # data 轉成 JSON 相容（datetime → ISO 字串）且不共用呼叫端的 dict
    data: dict[str, Any] = {"at": fake_clock.now(), "nested": {"id": UUID(int=5)}}
    out = envelope("homework.progress_updated", data, clock=fake_clock)
    assert out["data"] == {
        "at": "2026-09-01T01:00:00+00:00",
        "nested": {"id": "00000000-0000-0000-0000-000000000005"},
    }
    assert out["data"] is not data


def test_publish_after_commit_only(
    db_session: Session, fake_clock: FakeClock, published: list[Call]
) -> None:
    broadcast_after_commit(
        db_session,
        topic="homework",
        type="homework.progress_updated",
        data={"x": 1},
        clock=fake_clock,
    )
    assert published == []

    db_session.commit()
    assert published == [
        (["admin:homework"], envelope("homework.progress_updated", {"x": 1}, clock=fake_clock))
    ]

    broadcast_after_commit(
        db_session,
        topic="homework",
        type="homework.progress_updated",
        data={"x": 2},
        clock=fake_clock,
    )
    db_session.rollback()
    assert len(published) == 1
    db_session.commit()
    assert len(published) == 1


def test_publish_parent_split(
    db_session: Session, fake_clock: FakeClock, published: list[Call]
) -> None:
    s = uuid4()
    full = {"overall": "done", "staff_name": "林老師", "note": "內部備註"}

    broadcast_after_commit(
        db_session,
        topic="homework",
        type="homework.progress_updated",
        data=full,
        clock=fake_clock,
        student_id=s,
        parent_data={"overall": "done"},
    )
    db_session.commit()

    assert published == [
        (["admin:homework"], envelope("homework.progress_updated", full, clock=fake_clock)),
        (
            [f"student:{s}"],
            envelope("homework.progress_updated", {"overall": "done"}, clock=fake_clock),
        ),
    ]
    assert "staff_name" not in published[1][1]["data"]

    # 只給 student_id 沒給 parent_data：只送 admin
    published.clear()
    broadcast_after_commit(
        db_session,
        topic="pickup",
        type="pickup.request_updated",
        data={"id": 1},
        clock=fake_clock,
        student_id=s,
    )
    db_session.commit()
    assert [channels for channels, _ in published] == [["admin:pickup"]]


def test_publish_validation(
    db_session: Session, fake_clock: FakeClock, published: list[Call]
) -> None:
    s = uuid4()
    with pytest.raises(ValueError, match="pickup\\."):
        broadcast_after_commit(
            db_session,
            topic="pickup",
            type="attendance.checked_in",
            data={},
            clock=fake_clock,
        )
    with pytest.raises(ValueError, match="type"):
        envelope("", {}, clock=fake_clock)
    db_session.commit()
    assert published == []

    broadcast_after_commit(
        db_session,
        topic="attendance",
        type="attendance.updated",
        data={"status": "present", "staff_name": "林老師"},
        clock=fake_clock,
        student_id=s,
        parent_data={"status": "present"},
    )
    db_session.commit()
    assert [channels for channels, _ in published] == [["admin:attendance"], [f"student:{s}"]]
    assert published[1][1]["data"] == {"status": "present"}


def test_publish_push_to_recipient(
    db_session: Session, fake_clock: FakeClock, published: list[Call]
) -> None:
    p = uuid4()
    st = uuid4()
    push_to_recipient_after_commit(
        db_session,
        Recipient("parent", p),
        "notification.created",
        {"id": UUID(int=9), "title": "王小明 已到班"},
        clock=fake_clock,
    )
    push_to_recipient_after_commit(
        db_session, Recipient("staff", st), "notification.created", {"id": 1}, clock=fake_clock
    )
    assert published == []

    db_session.commit()

    assert published == [
        (
            [f"parent:{p}"],
            envelope(
                "notification.created",
                {"id": UUID(int=9), "title": "王小明 已到班"},
                clock=fake_clock,
            ),
        ),
        ([f"staff:{st}"], envelope("notification.created", {"id": 1}, clock=fake_clock)),
    ]


def test_publish_push_to_student(
    db_session: Session, fake_clock: FakeClock, published: list[Call]
) -> None:
    s = uuid4()
    push_to_student_after_commit(
        db_session,
        s,
        topic="attendance",
        type="attendance.updated",
        data={"status": "present"},
        clock=fake_clock,
    )
    db_session.commit()
    assert published == [
        (
            [student_channel(s)],
            envelope("attendance.updated", {"status": "present"}, clock=fake_clock),
        )
    ]

    with pytest.raises(ValueError, match="attendance\\."):
        push_to_student_after_commit(
            db_session, s, topic="attendance", type="pickup.x", data={}, clock=fake_clock
        )
    db_session.commit()
    assert len(published) == 1
