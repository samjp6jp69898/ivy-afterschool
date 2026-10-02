"""BACKEND-223：WebSocket 廣播（architecture_decisions §7）。

單一實例 in-process broadcaster。介面與 ivy ``utils/broadcast`` 相同、保留多實例時換成
RedisBroadcaster 的接縫但不實作（domain_spec §5 open question）。

- ``publish`` 只送到訂閱該 channel 的連線，回傳成功送達數。
- ``send_text`` 逾時視為僵死連線，從所有 channel 移除（避免 head-of-line blocking）。
- 訊息 JSON 超過 16 KiB → ValueError（WS 只送事件摘要，大資料由前端再打 REST 取得）。
- ``publish_threadsafe`` 讓 sync 程式碼（threadpool 端點、after-commit callback、背景執行緒）
  fire-and-forget；任何失敗只記 log，不可把已 commit 的操作變成錯誤回應。
"""

from __future__ import annotations

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from concurrent.futures import Future
from typing import Any, Protocol

logger = logging.getLogger(__name__)

MAX_MESSAGE_BYTES = 16 * 1024
DEFAULT_SEND_TIMEOUT = 2.0


class WebSocketLike(Protocol):
    """starlette ``WebSocket`` 的最小介面；測試用 FakeWebSocket 也符合。"""

    async def send_text(self, data: str) -> None: ...


class Broadcaster(ABC):
    @abstractmethod
    async def publish(self, channel: str, message: Mapping[str, Any]) -> int:
        """送到訂閱 channel 的所有連線，回傳成功送達數。"""

    @abstractmethod
    async def publish_many(self, channels: Sequence[str], message: Mapping[str, Any]) -> int:
        """channel 去重；同一連線訂閱多個目標 channel 時只收一次。"""

    @abstractmethod
    def subscribe(self, channel: str, ws: WebSocketLike) -> None: ...

    @abstractmethod
    def unsubscribe(self, ws: WebSocketLike, channel: str | None = None) -> None:
        """channel=None → 從全部 channel 移除。"""

    @abstractmethod
    def subscriber_count(self, channel: str) -> int: ...

    @abstractmethod
    async def start(self) -> None:
        """lifespan startup hook（Redis 實作在此建連線）。"""

    @abstractmethod
    async def stop(self) -> None:
        """lifespan shutdown hook。"""


def encode_message(message: Mapping[str, Any], *, max_bytes: int = MAX_MESSAGE_BYTES) -> str:
    body = json.dumps(message, ensure_ascii=False, default=str)
    size = len(body.encode("utf-8"))
    if size > max_bytes:
        raise ValueError(
            f"WS 訊息過大：{size} bytes > {max_bytes} bytes（{max_bytes // 1024} KiB）"
        )
    return body


class LocalBroadcaster(Broadcaster):
    def __init__(
        self,
        *,
        send_timeout: float = DEFAULT_SEND_TIMEOUT,
        max_message_bytes: int = MAX_MESSAGE_BYTES,
    ) -> None:
        self.send_timeout = send_timeout
        self.max_message_bytes = max_message_bytes
        # 以 list 保序；同一 channel 不重複收錄同一連線
        self._subscribers: dict[str, list[WebSocketLike]] = {}

    def subscribe(self, channel: str, ws: WebSocketLike) -> None:
        subscribers = self._subscribers.setdefault(channel, [])
        if not any(existing is ws for existing in subscribers):
            subscribers.append(ws)

    def unsubscribe(self, ws: WebSocketLike, channel: str | None = None) -> None:
        channels = [channel] if channel is not None else list(self._subscribers)
        for name in channels:
            subscribers = self._subscribers.get(name)
            if subscribers is None:
                continue
            subscribers[:] = [existing for existing in subscribers if existing is not ws]
            if not subscribers:
                del self._subscribers[name]

    def subscriber_count(self, channel: str) -> int:
        return len(self._subscribers.get(channel, ()))

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def publish(self, channel: str, message: Mapping[str, Any]) -> int:
        return await self.publish_many([channel], message)

    async def publish_many(self, channels: Sequence[str], message: Mapping[str, Any]) -> int:
        body = encode_message(message, max_bytes=self.max_message_bytes)
        targets: list[WebSocketLike] = []
        for channel in dict.fromkeys(channels):
            for ws in self._subscribers.get(channel, ()):
                if not any(existing is ws for existing in targets):
                    targets.append(ws)
        delivered = 0
        for ws in targets:
            if await self._send(ws, body):
                delivered += 1
            else:
                self.unsubscribe(ws)
        return delivered

    async def _send(self, ws: WebSocketLike, body: str) -> bool:
        try:
            await asyncio.wait_for(ws.send_text(body), timeout=self.send_timeout)
        except TimeoutError:
            logger.warning("WS 送出逾時 %.1fs，視為僵死連線移除", self.send_timeout)
            return False
        except Exception as exc:
            logger.info("WS 送出失敗，移除連線：%s", exc)
            return False
        return True


_broadcaster: Broadcaster | None = None
_main_loop: asyncio.AbstractEventLoop | None = None


def get_broadcaster() -> Broadcaster:
    """模組單例。多實例部署時改在此回傳 RedisBroadcaster（domain_spec §5 open question）。"""
    global _broadcaster
    if _broadcaster is None:
        _broadcaster = LocalBroadcaster()
    return _broadcaster


def reset_broadcaster_for_tests() -> None:
    global _broadcaster
    _broadcaster = None


def set_main_loop(loop: asyncio.AbstractEventLoop | None) -> None:
    """由 lifespan（BACKEND-020）在啟動時設定、關閉時清掉。"""
    global _main_loop
    _main_loop = loop


def _log_publish_result(future: Future[int]) -> None:
    exc = future.exception()
    if exc is not None:
        logger.warning("WS 背景廣播失敗：%s", exc)


def publish_threadsafe(channels: Sequence[str], message: Mapping[str, Any]) -> None:
    """從 sync 程式碼 fire-and-forget 廣播；沒有 main loop（單元測試 / CLI）時記 debug 後丟棄。"""
    try:
        loop = _main_loop
        if loop is None or loop.is_closed() or not loop.is_running():
            logger.debug("沒有 main loop，丟棄 WS 廣播 channels=%s", list(channels))
            return
        coro = get_broadcaster().publish_many(list(channels), dict(message))
        try:
            future = asyncio.run_coroutine_threadsafe(coro, loop)
        except Exception:
            coro.close()
            raise
        future.add_done_callback(_log_publish_result)
    except Exception:
        logger.warning("WS 廣播排程失敗 channels=%s", list(channels), exc_info=True)
