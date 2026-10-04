"""BACKEND-208：app/notifications/dispatcher.py（OutboxDispatcher.dispatch_due）。

撿取到期的 pending / failed outbox（for update skip locked）→ LINE push → sent / 指數退避 failed /
dead；家長停用、LINE 未設定直接 dead 並寫 last_error。只 flush 不 commit。
"""

import queue
import threading
from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.crypto import derive_key
from app.models.notifications import Notification, NotificationOutbox
from app.models.parents import ParentAccount
from app.notifications.channels.line import LineSendResult
from app.notifications.dispatcher import BACKOFF, MAX_ATTEMPTS, DispatchStats, dispatch_due
from app.notifications.events import Event
from app.notifications.templates import render
from tests.integration.db.conftest import connect_owner
from tests.support.factories import make_parent
from tests.support.fake_clock import FakeClock
from tests.support.fake_line import FakeLineMessagingClient

_PAYLOAD: dict[str, Any] = {"student_name": "王小明", "student_id": str(uuid4())}


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@127.0.0.1:54342/postgres")
    monkeypatch.setenv("APP_SECRET_KEY", "s" * 48)
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://127.0.0.1:5341")
    monkeypatch.setenv("R2_ENDPOINT_URL", "http://127.0.0.1:54344")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "afterschool")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "afterschool-local-secret")
    monkeypatch.setenv("R2_BUCKET", "afterschool-local")
    get_settings.cache_clear()
    derive_key.cache_clear()
    yield
    get_settings.cache_clear()
    derive_key.cache_clear()


def _make_outbox(
    session: Session,
    parent: ParentAccount,
    clock: FakeClock,
    *,
    event: Event = Event.HOMEWORK_DONE,
    recipient_id: UUID | None = None,
    next_attempt_at: datetime | None = None,
) -> NotificationOutbox:
    rendered = render(event, _PAYLOAD)
    notification = Notification(
        recipient_type="parent",
        recipient_id=recipient_id or parent.id,
        event=event.value,
        title=rendered.title,
        body=rendered.body,
        payload=_PAYLOAD,
    )
    session.add(notification)
    session.flush()
    outbox = NotificationOutbox(
        notification_id=notification.id,
        next_attempt_at=next_attempt_at or clock.now(),
    )
    session.add(outbox)
    session.flush()
    return outbox


def _dispatch(
    session: Session,
    clock: FakeClock,
    client: FakeLineMessagingClient | None,
    **kwargs: Any,
) -> DispatchStats:
    return dispatch_due(session, clock=clock, settings=get_settings(), line_client=client, **kwargs)


def _reload(session: Session, outbox_id: UUID) -> NotificationOutbox:
    session.expire_all()
    return session.execute(
        select(NotificationOutbox).where(NotificationOutbox.id == outbox_id)
    ).scalar_one()


def test_dispatch_sent(db_session: Session, fake_clock: FakeClock) -> None:
    parent = make_parent(db_session)
    outbox = _make_outbox(db_session, parent, fake_clock)
    client = FakeLineMessagingClient()

    stats = _dispatch(db_session, fake_clock, client)

    assert stats == DispatchStats(sent=1, retried=0, dead=0)
    row = _reload(db_session, outbox.id)
    assert row.status == "sent"
    assert row.attempts == 1
    assert row.last_error is None
    assert len(client.calls) == 1
    to, text, retry_key = client.calls[0]
    assert to == parent.line_user_id
    assert text.startswith("王小明 作業已完成\n")
    assert text.endswith("http://127.0.0.1:5341/parent/#/homework")
    assert retry_key == outbox.id
    # 已 sent 的列不再處理
    assert _dispatch(db_session, fake_clock, client) == DispatchStats(0, 0, 0)
    assert len(client.calls) == 1


def test_dispatch_backoff_sequence(db_session: Session, fake_clock: FakeClock) -> None:
    assert MAX_ATTEMPTS == 5
    assert (
        timedelta(seconds=30),
        timedelta(minutes=2),
        timedelta(minutes=10),
        timedelta(minutes=30),
        timedelta(hours=2),
    ) == BACKOFF
    parent = make_parent(db_session)
    outbox = _make_outbox(db_session, parent, fake_clock)
    client = FakeLineMessagingClient(LineSendResult(False, True, "HTTP 500: boom"))

    expected_delays = [
        timedelta(seconds=30),
        timedelta(minutes=2),
        timedelta(minutes=10),
        timedelta(minutes=30),
    ]
    for attempt, delay in enumerate(expected_delays, start=1):
        stats = _dispatch(db_session, fake_clock, client)
        assert stats == DispatchStats(sent=0, retried=1, dead=0)
        row = _reload(db_session, outbox.id)
        assert row.status == "failed"
        assert row.attempts == attempt
        assert row.next_attempt_at == fake_clock.now() + delay
        assert row.last_error == "HTTP 500: boom"
        # 未到期不處理
        fake_clock.advance(delay - timedelta(seconds=1))
        assert _dispatch(db_session, fake_clock, client) == DispatchStats(0, 0, 0)
        fake_clock.advance(seconds=1)

    stats = _dispatch(db_session, fake_clock, client)
    assert stats == DispatchStats(sent=0, retried=0, dead=1)
    row = _reload(db_session, outbox.id)
    assert row.status == "dead"
    assert row.attempts == 5
    assert row.last_error == "HTTP 500: boom"
    assert len(client.calls) == 5
    # dead 不再處理
    fake_clock.advance(hours=3)
    assert _dispatch(db_session, fake_clock, client) == DispatchStats(0, 0, 0)


def test_dispatch_retry_then_success(db_session: Session, fake_clock: FakeClock) -> None:
    parent = make_parent(db_session)
    outbox = _make_outbox(db_session, parent, fake_clock)
    client = FakeLineMessagingClient(LineSendResult(False, True, "timeout"))
    _dispatch(db_session, fake_clock, client)
    fake_clock.advance(seconds=30)

    client.result = LineSendResult(True, False, None)
    assert _dispatch(db_session, fake_clock, client) == DispatchStats(sent=1, retried=0, dead=0)
    row = _reload(db_session, outbox.id)
    assert row.status == "sent"
    assert row.attempts == 2
    assert row.last_error is None


def test_dispatch_permanent_and_inactive(db_session: Session, fake_clock: FakeClock) -> None:
    parent = make_parent(db_session)
    disabled = make_parent(db_session, status="disabled")
    permanent = _make_outbox(db_session, parent, fake_clock)
    inactive = _make_outbox(db_session, disabled, fake_clock)
    missing = _make_outbox(db_session, parent, fake_clock, recipient_id=uuid4())
    client = FakeLineMessagingClient(LineSendResult(False, False, "HTTP 403: blocked"))

    stats = _dispatch(db_session, fake_clock, client)

    assert stats == DispatchStats(sent=0, retried=0, dead=3)
    perm = _reload(db_session, permanent.id)
    assert perm.status == "dead"
    assert perm.attempts == 1
    assert perm.last_error == "HTTP 403: blocked"
    inact = _reload(db_session, inactive.id)
    assert inact.status == "dead"
    assert inact.last_error == "parent_inactive"
    miss = _reload(db_session, missing.id)
    assert miss.status == "dead"
    assert miss.last_error == "parent_inactive"
    # 停用 / 不存在的家長不會呼叫 LINE
    assert [c[2] for c in client.calls] == [permanent.id]

    unconfigured = _make_outbox(db_session, parent, fake_clock)
    assert _dispatch(db_session, fake_clock, None) == DispatchStats(sent=0, retried=0, dead=1)
    row = _reload(db_session, unconfigured.id)
    assert row.status == "dead"
    assert row.last_error == "line_not_configured"
    assert row.attempts == 0


def test_dispatch_long_error_truncated(db_session: Session, fake_clock: FakeClock) -> None:
    parent = make_parent(db_session)
    outbox = _make_outbox(db_session, parent, fake_clock)
    client = FakeLineMessagingClient(LineSendResult(False, True, "x" * 3000))

    _dispatch(db_session, fake_clock, client)

    row = _reload(db_session, outbox.id)
    assert row.last_error is not None
    assert len(row.last_error) == 2000

    # 可重試但沒帶錯誤訊息：failed 列仍要有 last_error（DB CHECK）
    other = _make_outbox(db_session, parent, fake_clock)
    _dispatch(db_session, fake_clock, FakeLineMessagingClient(LineSendResult(False, True, None)))
    assert _reload(db_session, other.id).last_error


def test_dispatch_not_due_and_ids(db_session: Session, fake_clock: FakeClock) -> None:
    parent = make_parent(db_session)
    future = _make_outbox(
        db_session, parent, fake_clock, next_attempt_at=fake_clock.now() + timedelta(minutes=1)
    )
    a = _make_outbox(db_session, parent, fake_clock)
    b = _make_outbox(db_session, parent, fake_clock)
    client = FakeLineMessagingClient()

    assert _dispatch(db_session, fake_clock, client, ids=[a.id]) == DispatchStats(1, 0, 0)
    assert _reload(db_session, a.id).status == "sent"
    assert _reload(db_session, b.id).status == "pending"
    assert _reload(db_session, future.id).status == "pending"
    assert [c[2] for c in client.calls] == [a.id]

    # limit 生效：剩 b 到期、future 未到期
    assert _dispatch(db_session, fake_clock, client, limit=1) == DispatchStats(1, 0, 0)
    assert _reload(db_session, b.id).status == "sent"
    assert _reload(db_session, future.id).status == "pending"

    fake_clock.advance(minutes=1)
    assert _dispatch(db_session, fake_clock, client) == DispatchStats(1, 0, 0)
    assert _reload(db_session, future.id).status == "sent"
    # ids 為空清單：不處理任何列
    extra = _make_outbox(db_session, parent, fake_clock)
    assert _dispatch(db_session, fake_clock, client, ids=[]) == DispatchStats(0, 0, 0)
    assert _reload(db_session, extra.id).status == "pending"


@pytest.fixture
def owner_delete_parents() -> Iterator[list[UUID]]:
    """committing 測試建立的家長以 owner 連線刪除；排在 committing_db_session 之前。"""
    ids: list[UUID] = []
    yield ids
    with connect_owner() as conn:
        conn.execute("set lock_timeout = '5s'")
        for parent_id in ids:
            conn.execute("delete from public.parent_accounts where id = %s", (parent_id,))
        conn.commit()


@pytest.mark.cleanup_tables("notification_outbox", "notifications")
def test_dispatch_skip_locked_concurrent(
    owner_delete_parents: list[UUID],
    committing_db_session: Session,
    db_engine: Engine,
    fake_clock: FakeClock,
) -> None:
    """兩條連線同時 dispatch 同一列：先鎖到的送出（送出期間持鎖），另一條 skip locked 不重送。"""
    parent = make_parent(committing_db_session)
    outbox = _make_outbox(committing_db_session, parent, fake_clock)
    committing_db_session.commit()
    owner_delete_parents.append(parent.id)

    entered = threading.Event()
    release = threading.Event()

    def hold_until_released() -> None:
        entered.set()
        release.wait(timeout=10)

    client = FakeLineMessagingClient(before_return=hold_until_released)
    stats: dict[int, DispatchStats] = {}
    done: queue.Queue[int] = queue.Queue()
    barrier = threading.Barrier(2)

    def worker(index: int) -> None:
        session = Session(bind=db_engine)
        try:
            barrier.wait(timeout=10)
            stats[index] = _dispatch(session, fake_clock, client, ids=[outbox.id])
            session.commit()
        finally:
            session.close()
            done.put(index)

    threads = [threading.Thread(target=worker, args=(i,)) for i in (0, 1)]
    for t in threads:
        t.start()
    try:
        assert entered.wait(timeout=10)
        # 持鎖的那條還卡在 LINE push；另一條應該已經 skip 完成
        skipper = done.get(timeout=10)
        assert stats[skipper] == DispatchStats(0, 0, 0)
        assert len(client.calls) == 1
    finally:
        release.set()
        for t in threads:
            t.join(timeout=10)
    sender = done.get(timeout=10)
    assert sender != skipper
    assert stats[sender] == DispatchStats(1, 0, 0)
    assert len(client.calls) == 1
    committing_db_session.expire_all()
    row = committing_db_session.execute(
        select(NotificationOutbox).where(NotificationOutbox.id == outbox.id)
    ).scalar_one()
    assert row.status == "sent"
    assert row.attempts == 1
