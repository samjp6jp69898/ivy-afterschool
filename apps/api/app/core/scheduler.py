"""BACKEND-018：APScheduler 背景工作註冊、啟停與 advisory lock 冪等 runner。

architecture_decisions §7：lifespan 內輕量排程 + DB 鎖確保冪等。工作是同步 DB 操作，跑在
``BackgroundScheduler`` 的執行緒（與 uvicorn 單 worker 共用 process，工作必須短、長工作要切批）。

- ``@scheduled_job(job_id, trigger, run_key=...)``：註冊到 module 層 ``JOB_REGISTRY``；
  各工作模組在 ``app/jobs/__init__.py`` 中 import，BACKEND-020 的 lifespan 在啟動前 import
  ``app.jobs``。
- ``run_job_once``：開 session → ``try_advisory_xact_lock(f"job:{job_id}", run_key(clock))``
  （BACKEND-017）→ 取不到回 ``skipped``；取到 → ``func(session, clock)`` → commit → ``ran``；
  例外 → rollback + ``logger.exception`` + Sentry → ``failed``（不往外拋，避免殺掉 scheduler
  執行緒）。
- run_key：interval 工作回固定 ``'tick'``；每日工作回 ``clock.today().isoformat()``（台北日）。
  每日工作「同日只跑一次」由工作本身的冪等寫法（upsert）保證，advisory lock 只防同時執行。
- ``APP_ENV=test`` 時 lifespan 不啟動 scheduler；測試直接呼叫 ``run_job_once``。

移植 ivy ``utils/advisory_lock.py::try_scheduler_lock`` 的「取不到鎖就跳過」語意。
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import partial
from typing import Final, Literal

import sentry_sdk
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.base import BaseTrigger
from sqlalchemy.orm import Session

from app.core.clock import TAIPEI, Clock
from app.core.locks import try_advisory_xact_lock

logger = logging.getLogger(__name__)

JobFunc = Callable[[Session, Clock], None]
RunKey = Callable[[Clock], str]
JobResult = Literal["ran", "skipped", "failed"]

_JOB_ID_RE: Final = re.compile(r"^[a-z_.]+$")
_MISFIRE_GRACE_SECONDS: Final = 60


def tick_run_key(clock: Clock) -> str:
    """interval 工作的冪等鍵：固定字串，只防同時執行。"""
    return "tick"


def daily_run_key(clock: Clock) -> str:
    """每日工作的冪等鍵：台北日期。"""
    return clock.today().isoformat()


@dataclass(frozen=True)
class JobSpec:
    job_id: str
    trigger: BaseTrigger
    func: JobFunc
    run_key: RunKey = tick_run_key


JOB_REGISTRY: dict[str, JobSpec] = {}


def scheduled_job(
    job_id: str,
    trigger: BaseTrigger,
    *,
    run_key: RunKey = tick_run_key,
    registry: dict[str, JobSpec] = JOB_REGISTRY,
) -> Callable[[JobFunc], JobFunc]:
    """decorator：把工作註冊進 registry；job_id 不合法或重複 → ValueError。"""
    if not _JOB_ID_RE.fullmatch(job_id):
        raise ValueError(f"job_id 只接受 ^[a-z_.]+$：{job_id!r}")

    def decorator(func: JobFunc) -> JobFunc:
        if job_id in registry:
            raise ValueError(f"背景工作 job_id 重複註冊：{job_id}")
        registry[job_id] = JobSpec(job_id=job_id, trigger=trigger, func=func, run_key=run_key)
        return func

    return decorator


def run_job_once(
    spec: JobSpec, *, session_factory: Callable[[], Session], clock: Clock
) -> JobResult:
    session = session_factory()
    try:
        if not try_advisory_xact_lock(session, f"job:{spec.job_id}", spec.run_key(clock)):
            session.rollback()
            return "skipped"
        spec.func(session, clock)
        session.commit()
        return "ran"
    except Exception as exc:
        session.rollback()
        logger.exception("背景工作 %s 執行失敗", spec.job_id)
        sentry_sdk.capture_exception(exc)
        return "failed"
    finally:
        session.close()


def start_scheduler(
    *,
    session_factory: Callable[[], Session],
    clock: Clock,
    registry: Mapping[str, JobSpec] = JOB_REGISTRY,
) -> BackgroundScheduler:
    """依 registry 加入全部工作（coalesce、max_instances=1、misfire_grace_time=60）並啟動。"""
    scheduler = BackgroundScheduler(timezone=TAIPEI)
    for spec in registry.values():
        scheduler.add_job(
            partial(run_job_once, spec, session_factory=session_factory, clock=clock),
            trigger=spec.trigger,
            id=spec.job_id,
            name=spec.job_id,
            coalesce=True,
            max_instances=1,
            misfire_grace_time=_MISFIRE_GRACE_SECONDS,
        )
    scheduler.start()
    return scheduler


def shutdown_scheduler(scheduler: BackgroundScheduler) -> None:
    scheduler.shutdown(wait=False)
