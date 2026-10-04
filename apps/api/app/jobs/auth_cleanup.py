"""BACKEND-066：refresh token 過期 / 撤銷列的清理背景工作。

移植 ivy ``api/parent_portal/auth.py::gc_expired_refresh_tokens``：保留 7 天供重用偵測與調查，
超出保留窗的列（``expires_at`` 或 ``revoked_at`` 早於 now - 7 天）刪除。``replaced_by`` 自參照為
on delete set null，刪除順序不影響。

- 單次最多刪 ``CLEANUP_BATCH_SIZE`` 筆，剩餘留待下次執行（每日 03:30 台北時間）。
- 只 ``flush``、不 commit：BACKEND-018 runner 的 advisory lock 是交易級，工作函式自行 commit
  會提前釋放鎖，由 ``run_job_once`` 負責 commit / rollback。
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Final, cast

from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import CursorResult, delete, or_, select
from sqlalchemy.orm import Session

from app.core.clock import Clock
from app.core.scheduler import daily_run_key, scheduled_job
from app.models.account import RefreshToken

logger = logging.getLogger(__name__)

RETENTION: Final = timedelta(days=7)
CLEANUP_BATCH_SIZE = 5000


def cleanup_refresh_tokens(session: Session, clock: Clock) -> int:
    """刪除超出保留窗的列（最多 CLEANUP_BATCH_SIZE 筆），回傳刪除數；只 flush。"""
    cutoff = clock.now() - RETENTION
    victims = (
        select(RefreshToken.id)
        .where(or_(RefreshToken.expires_at < cutoff, RefreshToken.revoked_at < cutoff))
        .limit(CLEANUP_BATCH_SIZE)
    )
    result = session.execute(delete(RefreshToken).where(RefreshToken.id.in_(victims)))
    session.flush()
    return int(cast(CursorResult[Any], result).rowcount or 0)


@scheduled_job(
    "auth.refresh_token_cleanup",
    CronTrigger(hour=3, minute=30, timezone="Asia/Taipei"),
    run_key=daily_run_key,
)
def run_refresh_token_cleanup(session: Session, clock: Clock) -> None:
    deleted = cleanup_refresh_tokens(session, clock)
    logger.info("refresh token 清理：刪除 %d 筆", deleted)
