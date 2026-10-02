"""BACKEND-223：Broadcaster 介面、LocalBroadcaster 與跨執行緒發佈。"""

import asyncio
import threading
from collections.abc import Iterator

import pytest
from app.realtime.broadcaster import (
    Broadcaster,
    LocalBroadcaster,
    get_broadcaster,
    publish_threadsafe,
    reset_broadcaster_for_tests,
    set_main_loop,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class FakeWebSocket:
    def __init__(self, *, delay: float = 0.0) -> None:
        self.sent: list[str] = []
        self._delay = delay

    async def send_text(self, data: str) -> None:
        if self._delay:
            await asyncio.sleep(self._delay)
        self.sent.append(data)


@pytest.fixture
def broadcaster() -> LocalBroadcaster:
    return LocalBroadcaster()


@pytest.fixture
def clean_singleton() -> Iterator[None]:
    reset_broadcaster_for_tests()
    set_main_loop(None)
    yield
    set_main_loop(None)
    reset_broadcaster_for_tests()


@pytest.mark.anyio
async def test_broadcaster_publish_subscribed_only(broadcaster: LocalBroadcaster) -> None:
    a, b = FakeWebSocket(), FakeWebSocket()
    broadcaster.subscribe("admin:pickup", a)
    broadcaster.subscribe("admin:homework", b)

    delivered = await broadcaster.publish("admin:pickup", {"type": "pickup.updated"})

    assert delivered == 1
    assert a.sent == ['{"type": "pickup.updated"}']
    assert b.sent == []


@pytest.mark.anyio
async def test_broadcaster_unsubscribe(broadcaster: LocalBroadcaster) -> None:
    a = FakeWebSocket()
    broadcaster.subscribe("admin:pickup", a)
    broadcaster.subscribe("admin:homework", a)

    broadcaster.unsubscribe(a, "admin:pickup")
    assert await broadcaster.publish("admin:pickup", {"type": "x"}) == 0
    assert broadcaster.subscriber_count("admin:pickup") == 0
    assert broadcaster.subscriber_count("admin:homework") == 1

    broadcaster.unsubscribe(a)
    assert broadcaster.subscriber_count("admin:homework") == 0
    assert await broadcaster.publish("admin:homework", {"type": "x"}) == 0
    assert a.sent == []


def test_broadcaster_subscribe_idempotent(broadcaster: LocalBroadcaster) -> None:
    a = FakeWebSocket()
    broadcaster.subscribe("admin:pickup", a)
    broadcaster.subscribe("admin:pickup", a)

    assert broadcaster.subscriber_count("admin:pickup") == 1
    # 沒訂閱過的連線 / 頻道，unsubscribe 不拋例外
    broadcaster.unsubscribe(FakeWebSocket(), "nope")
    assert broadcaster.subscriber_count("nope") == 0


@pytest.mark.anyio
async def test_broadcaster_publish_many_dedupe(broadcaster: LocalBroadcaster) -> None:
    a, b = FakeWebSocket(), FakeWebSocket()
    broadcaster.subscribe("parent:p1", a)
    broadcaster.subscribe("student:s1", a)
    broadcaster.subscribe("student:s1", b)

    delivered = await broadcaster.publish_many(
        ["parent:p1", "student:s1", "parent:p1"], {"type": "homework.updated"}
    )

    assert delivered == 2
    assert len(a.sent) == 1
    assert len(b.sent) == 1


@pytest.mark.anyio
async def test_broadcaster_slow_consumer_removed() -> None:
    broadcaster = LocalBroadcaster(send_timeout=0.05)
    slow, fast = FakeWebSocket(delay=3.0), FakeWebSocket()
    broadcaster.subscribe("admin:pickup", slow)
    broadcaster.subscribe("admin:pickup", fast)
    broadcaster.subscribe("admin:homework", slow)

    delivered = await broadcaster.publish("admin:pickup", {"type": "pickup.updated"})

    assert delivered == 1
    assert fast.sent == ['{"type": "pickup.updated"}']
    assert slow.sent == []
    # 僵死連線從所有頻道移除
    assert broadcaster.subscriber_count("admin:pickup") == 1
    assert broadcaster.subscriber_count("admin:homework") == 0


def test_broadcaster_default_send_timeout() -> None:
    assert LocalBroadcaster().send_timeout == 2.0


@pytest.mark.anyio
async def test_broadcaster_payload_too_large(broadcaster: LocalBroadcaster) -> None:
    a = FakeWebSocket()
    broadcaster.subscribe("admin:pickup", a)

    with pytest.raises(ValueError, match="16"):
        await broadcaster.publish("admin:pickup", {"blob": "x" * (20 * 1024)})

    assert a.sent == []
    # 剛好在上限內可以送
    assert await broadcaster.publish("admin:pickup", {"blob": "x" * (16 * 1024 - 12)}) == 1


@pytest.mark.anyio
async def test_broadcaster_publish_threadsafe(clean_singleton: None) -> None:
    a = FakeWebSocket()
    get_broadcaster().subscribe("admin:pickup", a)
    set_main_loop(asyncio.get_running_loop())

    thread = threading.Thread(
        target=publish_threadsafe, args=(["admin:pickup"], {"type": "pickup.updated"})
    )
    thread.start()
    thread.join(timeout=2)

    for _ in range(50):
        if a.sent:
            break
        await asyncio.sleep(0.01)
    assert a.sent == ['{"type": "pickup.updated"}']

    set_main_loop(None)
    publish_threadsafe(["admin:pickup"], {"type": "dropped"})
    await asyncio.sleep(0.02)
    assert a.sent == ['{"type": "pickup.updated"}']


def test_broadcaster_publish_threadsafe_without_loop_logs(
    clean_singleton: None, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level("DEBUG", logger="app.realtime.broadcaster"):
        publish_threadsafe(["admin:pickup"], {"type": "dropped"})

    assert any("main loop" in rec.getMessage() for rec in caplog.records)


def test_broadcaster_get_broadcaster_singleton(clean_singleton: None) -> None:
    first = get_broadcaster()

    assert isinstance(first, Broadcaster)
    assert isinstance(first, LocalBroadcaster)
    assert get_broadcaster() is first
