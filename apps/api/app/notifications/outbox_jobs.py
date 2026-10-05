"""BACKEND-209：outbox 派送觸發（commit 後 ``kick_outbox`` 與每 30 秒 ``sweep_outbox``）。

移植 ivy ``services/notification/outbox_sweeper.py::tick_outbox_sweep`` 的撿漏；kick 改為 commit 後
直接觸發，sweep 只負責補送（kick 失敗、程序重啟、退避到期的列）。

- ``kick_outbox(ids)``：空清單直接 return；``thread``（預設）→ 提交到模組層
  ``ThreadPoolExecutor(max_workers=2)``，在新 ``session_scope`` 內 ``dispatch_due(ids=...)``；
  ``sync`` → 當場執行（測試）；``off`` → 不做事。kick 內部例外只記 log（commit 已完成，不能回頭讓
  請求失敗），未送出的列由 sweep 補送。
- ``sweep_outbox``：``@scheduled_job`` 每 30 秒 ``dispatch_due(limit=50)``；只 flush 不 commit
  （BACKEND-018 的 advisory lock 是交易級，由 runner commit）。
- LINE client：每次 kick / sweep 以 ``build_line_client(session)`` 取得，但以 channel access token
  為 key 快取單一實例（HttpLineMessagingClient 持有 httpx.Client 連線池，每批重建會累積連線）；
  token 變更後下一次即換新實例，舊實例以 BACKEND-543 的 ``close()`` 釋放連線池。
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import Final, Literal, get_args
from uuid import UUID

from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.orm import Session

from app.core.clock import Clock, get_clock
from app.core.config import get_settings
from app.core.db import session_scope
from app.core.scheduler import scheduled_job
from app.core.settings_registry import LINE_MESSAGING
from app.notifications.channels.line import LineMessagingClient, build_line_client
from app.notifications.dispatcher import dispatch_due
from app.services.settings_service import get_setting

logger = logging.getLogger(__name__)

KickMode = Literal["thread", "sync", "off"]
JOB_ID: Final = "notifications.outbox_sweep"
SWEEP_INTERVAL_SECONDS: Final = 30
SWEEP_LIMIT: Final = 50
_KICK_MODES: Final[frozenset[str]] = frozenset(get_args(KickMode))

_mode: KickMode = "thread"
_executor: Final = ThreadPoolExecutor(max_workers=2, thread_name_prefix="outbox-kick")
_client_lock: Final = threading.Lock()
_client_cache: tuple[str | None, LineMessagingClient | None] | None = None


def set_kick_mode(mode: KickMode) -> None:
    global _mode
    if mode not in _KICK_MODES:
        raise ValueError(f"kick mode 只接受 {sorted(_KICK_MODES)}：{mode!r}")
    _mode = mode


def reset_line_client_cache() -> None:
    """測試用：清掉快取的 LINE client（monkeypatch build_line_client 的測試之間互不影響）。"""
    global _client_cache
    with _client_lock:
        _client_cache = None


def _line_client(session: Session) -> LineMessagingClient | None:
    """以目前 token 為 key 的單一實例；token 變更才重建。"""
    global _client_cache
    token = get_setting(session, LINE_MESSAGING).channel_access_token
    with _client_lock:
        if _client_cache is not None and _client_cache[0] == token:
            return _client_cache[1]
        previous = _client_cache[1] if _client_cache is not None else None
        client = build_line_client(session)
        _client_cache = (token, client)
    close = getattr(previous, "close", None)
    if callable(close):
        close()
    return client


def _run_kick(ids: list[UUID]) -> None:
    try:
        with session_scope() as session:
            stats = dispatch_due(
                session,
                clock=get_clock(),
                settings=get_settings(),
                line_client=_line_client(session),
                limit=len(ids),
                ids=ids,
            )
        logger.debug("outbox kick ids=%d → %s", len(ids), stats)
    except Exception:
        # commit 已完成，不能讓請求失敗；未送出的列由 sweep 補送
        logger.exception("outbox kick 失敗 ids=%s", [str(i) for i in ids])


def kick_outbox(outbox_ids: Sequence[UUID]) -> None:
    ids = list(outbox_ids)
    if not ids or _mode == "off":
        return
    if _mode == "sync":
        _run_kick(ids)
        return
    _executor.submit(_run_kick, ids)


@scheduled_job(JOB_ID, IntervalTrigger(seconds=SWEEP_INTERVAL_SECONDS))
def sweep_outbox(session: Session, clock: Clock) -> None:
    stats = dispatch_due(
        session,
        clock=clock,
        settings=get_settings(),
        line_client=_line_client(session),
        limit=SWEEP_LIMIT,
    )
    if stats.sent or stats.retried or stats.dead:
        logger.info("outbox sweep：%s", stats)
