"""BACKEND-412：接送請求自動過期背景工作（domain_spec M7 流程 6）。

超過 ``pickup.window`` 的 ``auto_expire_minutes`` 仍未結束的請求，以及前一天殘留的非終態請求，一律
改為 expired。

- 單一條件式 UPDATE：``id IN (SELECT ... LIMIT 200 FOR UPDATE SKIP LOCKED)``，正在被其他交易處理
  （員工完成、家長取消）的列跳過、下一輪再處理；雙方都是條件式更新，只有一方命中。
- 對每筆過期請求 ``publish_request_change``（BACKEND-404）；不發通知。
- 只 flush、不 commit：BACKEND-018 runner 的 advisory lock 是交易級，由 ``run_job_once`` commit。
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Final

from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.core.clock import Clock
from app.core.scheduler import scheduled_job
from app.core.settings_registry import PICKUP_WINDOW
from app.models.pickup import OPEN_STATUSES, PickupRequest
from app.services.pickup.views import publish_request_change
from app.services.settings_service import get_setting

logger = logging.getLogger(__name__)

EXPIRE_BATCH_SIZE: Final = 200


@scheduled_job("pickup.expire_requests", IntervalTrigger(minutes=5))
def expire_overdue_requests(session: Session, clock: Clock) -> int:
    """回傳本輪過期筆數（最多 EXPIRE_BATCH_SIZE）。"""
    minutes = get_setting(session, PICKUP_WINDOW).auto_expire_minutes
    cutoff = clock.now() - timedelta(minutes=minutes)
    victims = (
        select(PickupRequest.id)
        .where(
            PickupRequest.status.in_(OPEN_STATUSES),
            or_(PickupRequest.created_at < cutoff, PickupRequest.service_date < clock.today()),
        )
        .order_by(PickupRequest.created_at, PickupRequest.id)
        .limit(EXPIRE_BATCH_SIZE)
        .with_for_update(skip_locked=True)
    )
    expired_ids = list(
        session.execute(
            update(PickupRequest)
            .where(PickupRequest.id.in_(victims), PickupRequest.status.in_(OPEN_STATUSES))
            .values(status="expired")
            .returning(PickupRequest.id)
        ).scalars()
    )
    if expired_ids:
        # bulk update 不經 ORM：以 DB 現值覆蓋同 session 內可能已載入的舊屬性
        requests = session.execute(
            select(PickupRequest)
            .where(PickupRequest.id.in_(expired_ids))
            .execution_options(populate_existing=True)
        ).scalars()
        for request in requests:
            publish_request_change(session, request, clock=clock)
    session.flush()
    logger.info("接送請求自動過期：%d 筆（auto_expire_minutes=%d）", len(expired_ids), minutes)
    return len(expired_ids)
