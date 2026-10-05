"""BACKEND-304：每日出勤初始化背景工作 attendance.daily_init。

營業日 05:00~20:30（台北）每 30 分鐘跑一次 BACKEND-303 ``initialize_daily_attendance``：第一次建立
當日全部出勤，之後的執行因冪等只補上當天新入班 / 復學的學生，也涵蓋程序重啟錯過排程的情況
（BACKEND-018 的 misfire_grace_time 只有 60 秒）。run_key 為台北日期。

只 flush、不 commit：BACKEND-018 runner 的 advisory lock 是交易級，由 ``run_job_once`` commit。
"""

from __future__ import annotations

import logging

from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session

from app.core.clock import Clock
from app.core.scheduler import daily_run_key, scheduled_job
from app.services.attendance_service import initialize_daily_attendance

logger = logging.getLogger(__name__)


@scheduled_job(
    "attendance.daily_init",
    CronTrigger(minute="0,30", hour="5-20", timezone="Asia/Taipei"),
    run_key=daily_run_key,
)
def run_daily_attendance_init(session: Session, clock: Clock) -> None:
    result = initialize_daily_attendance(session, clock.today(), clock=clock)
    logger.info(
        "每日出勤初始化 %s：skipped=%s expected=%d leave=%d",
        result.service_date,
        result.skipped,
        result.created_expected,
        result.created_leave,
    )
