"""BACKEND-018：app/core/scheduler.py（JobSpec 註冊、run_job_once 冪等 runner、start / shutdown）。

不依賴業務表：以 owner 連線建立探針 schema `scheduler_probe`（只做 DDL 與清表），
run_job_once 拿到的 session 一律由 app_backend 的 db_engine 建立。
"""

import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import psycopg
import pytest
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.core import scheduler as scheduler_module
from app.core.clock import Clock
from app.core.locks import try_advisory_xact_lock
from app.core.scheduler import (
    JobSpec,
    run_job_once,
    scheduled_job,
    shutdown_scheduler,
    start_scheduler,
)
from tests.support import db_urls
from tests.support.fake_clock import FakeClock

Conn = psycopg.Connection[tuple[Any, ...]]

SCHEMA = "scheduler_probe"
TABLE = f"{SCHEMA}.items"
JOB_ID = "probe.job"


def _owner_connect() -> Conn:
    conn = psycopg.connect(db_urls.to_psycopg_dsn(db_urls.owner_url()), autocommit=True)
    db_urls.assert_connected_loopback(conn.info.hostaddr)
    return conn


def _count() -> int:
    with _owner_connect() as conn:
        row = conn.execute(f"select count(*) from {TABLE}").fetchone()  # noqa: S608
    assert row is not None
    return int(row[0])


@pytest.fixture(scope="module", autouse=True)
def scheduler_probe() -> Iterator[None]:
    with _owner_connect() as conn:
        conn.execute(f"drop schema if exists {SCHEMA} cascade")
        conn.execute(f"create schema {SCHEMA}")
        conn.execute(f"create table {TABLE} (id serial primary key, name text)")
        conn.execute(f"grant usage on schema {SCHEMA} to app_backend")
        conn.execute(f"grant all on {TABLE} to app_backend")
        conn.execute(f"grant usage on sequence {SCHEMA}.items_id_seq to app_backend")
    yield
    with _owner_connect() as conn:
        conn.execute(f"drop schema {SCHEMA} cascade")


@pytest.fixture(autouse=True)
def truncate_probe() -> Iterator[None]:
    yield
    with _owner_connect() as conn:
        conn.execute(f"truncate {TABLE}")


@pytest.fixture
def session_factory(db_engine: Engine) -> Any:
    return lambda: Session(bind=db_engine, expire_on_commit=False)


def _insert(session: Session, clock: Clock) -> None:
    session.execute(
        text(f"insert into {TABLE} (name) values (:name)"),  # noqa: S608
        {"name": clock.today().isoformat()},
    )


class _Counter:
    def __init__(self, func: Any) -> None:
        self.calls = 0
        self._func = func

    def __call__(self, session: Session, clock: Clock) -> None:
        self.calls += 1
        self._func(session, clock)


def _spec(func: Any, **kwargs: Any) -> JobSpec:
    return JobSpec(job_id=JOB_ID, trigger=IntervalTrigger(minutes=5), func=func, **kwargs)


# --- 註冊 -------------------------------------------------------------------------------


def test_scheduler_register_duplicate() -> None:
    registry: dict[str, JobSpec] = {}

    @scheduled_job(JOB_ID, IntervalTrigger(minutes=5), registry=registry)
    def _first(session: Session, clock: Clock) -> None:
        return None

    assert set(registry) == {JOB_ID}
    spec = registry[JOB_ID]
    assert spec.func is _first
    assert spec.run_key(FakeClock(datetime(2026, 9, 1, tzinfo=UTC))) == "tick"
    assert isinstance(spec.trigger, IntervalTrigger)

    with pytest.raises(ValueError, match=JOB_ID):

        @scheduled_job(JOB_ID, IntervalTrigger(minutes=5), registry=registry)
        def _second(session: Session, clock: Clock) -> None:
            return None

    with pytest.raises(ValueError, match="job_id"):

        @scheduled_job("Bad Id", IntervalTrigger(minutes=5), registry=registry)
        def _third(session: Session, clock: Clock) -> None:
            return None

    assert set(registry) == {JOB_ID}


def test_scheduler_register_default_registry() -> None:
    registry_id = "probe.default_registry"
    try:

        @scheduled_job(registry_id, IntervalTrigger(minutes=5))
        def _job(session: Session, clock: Clock) -> None:
            return None

        assert scheduler_module.JOB_REGISTRY[registry_id].func is _job
    finally:
        scheduler_module.JOB_REGISTRY.pop(registry_id, None)


# --- run_job_once -----------------------------------------------------------------------


def test_scheduler_run_job_once_ran(session_factory: Any, fake_clock: FakeClock) -> None:
    func = _Counter(_insert)

    result = run_job_once(_spec(func), session_factory=session_factory, clock=fake_clock)

    assert result == "ran"
    assert func.calls == 1
    assert _count() == 1


def test_scheduler_run_job_once_skipped_when_locked(
    db_engine: Engine, session_factory: Any, fake_clock: FakeClock
) -> None:
    func = _Counter(_insert)
    holder = Session(bind=db_engine)
    try:
        assert try_advisory_xact_lock(holder, f"job:{JOB_ID}", "tick") is True

        result = run_job_once(_spec(func), session_factory=session_factory, clock=fake_clock)

        assert result == "skipped"
        assert func.calls == 0
        assert _count() == 0
    finally:
        holder.rollback()
        holder.close()

    # 鎖釋放後同一個 spec 可以執行
    assert run_job_once(_spec(func), session_factory=session_factory, clock=fake_clock) == "ran"
    assert func.calls == 1


def test_scheduler_run_job_once_failed_rolls_back(
    session_factory: Any, fake_clock: FakeClock, caplog: pytest.LogCaptureFixture
) -> None:
    def _insert_then_boom(session: Session, clock: Clock) -> None:
        _insert(session, clock)
        raise RuntimeError("job 爆炸")

    with caplog.at_level(logging.ERROR):
        result = run_job_once(
            _spec(_insert_then_boom), session_factory=session_factory, clock=fake_clock
        )

    assert result == "failed"
    assert _count() == 0
    assert JOB_ID in caplog.text
    assert "RuntimeError" in caplog.text


@pytest.mark.clock("2026-09-01T16:30:00+00:00")
def test_scheduler_daily_run_key_uses_taipei_date(
    session_factory: Any, fake_clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[tuple[str, str]] = []

    def _spy(session: Session, namespace: str, key: str) -> bool:
        captured.append((namespace, key))
        return try_advisory_xact_lock(session, namespace, key)

    monkeypatch.setattr(scheduler_module, "try_advisory_xact_lock", _spy)
    spec = _spec(_Counter(_insert), run_key=lambda c: c.today().isoformat())

    assert run_job_once(spec, session_factory=session_factory, clock=fake_clock) == "ran"

    # UTC 16:30 已是台北的次日
    assert captured == [(f"job:{JOB_ID}", "2026-09-02")]


# --- start / shutdown -------------------------------------------------------------------


def test_scheduler_start_adds_jobs(session_factory: Any, fake_clock: FakeClock) -> None:
    spec = _spec(_Counter(_insert))

    scheduler = start_scheduler(
        session_factory=session_factory, clock=fake_clock, registry={JOB_ID: spec}
    )
    try:
        assert isinstance(scheduler, BackgroundScheduler)
        assert scheduler.running is True
        job = scheduler.get_job(JOB_ID)
        assert job is not None
        assert job.max_instances == 1
        assert job.coalesce is True
        assert job.misfire_grace_time == 60
        assert isinstance(job.trigger, IntervalTrigger)
        assert str(scheduler.timezone) == "Asia/Taipei"
    finally:
        shutdown_scheduler(scheduler)

    assert scheduler.running is False
