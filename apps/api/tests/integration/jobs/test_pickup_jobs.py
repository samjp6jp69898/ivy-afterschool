"""BACKEND-412：app/jobs/pickup_jobs.py（接送請求自動過期背景工作）。

時間以 FakeClock 注入：created_at 依 clock.now() 回推設定；pickup.window 讀 seed 預設（120 分鐘），
每個測試前後清空設定快取。
"""

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

import app.jobs  # noqa: F401  觸發 @scheduled_job 註冊
from app.core.scheduler import JOB_REGISTRY
from app.core.tx_hooks import install_tx_hooks
from app.jobs.pickup_jobs import EXPIRE_BATCH_SIZE, expire_overdue_requests
from app.models.pickup import PickupRequest
from app.realtime import publish as publish_module
from app.realtime.publish import admin_topic_channel
from app.services.settings_service import clear_settings_cache
from tests.integration.db.conftest import connect_owner
from tests.support.factories import make_pickup_request, make_student
from tests.support.fake_clock import FakeClock

_TODAY = date(2026, 9, 10)
_NOW = datetime(2026, 9, 10, 9, 0, tzinfo=UTC)  # 台北 17:00
Call = tuple[list[str], dict[str, Any]]


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    clear_settings_cache()
    yield
    clear_settings_cache()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(_NOW)


@pytest.fixture
def published(monkeypatch: pytest.MonkeyPatch) -> list[Call]:
    install_tx_hooks()
    calls: list[Call] = []

    def record(channels: list[str], message: dict[str, Any]) -> None:
        calls.append((list(channels), dict(message)))

    monkeypatch.setattr(publish_module, "publish_threadsafe", record)
    return calls


def _request(
    session: Session,
    *,
    minutes_ago: int,
    status: str = "pending",
    service_date: date = _TODAY,
    reply_source: str | None = None,
) -> PickupRequest:
    request = make_pickup_request(
        session,
        make_student(session),
        service_date=service_date,
        status=status,  # type: ignore[arg-type]
        reply_source=reply_source,  # type: ignore[arg-type]
    )
    request.created_at = _NOW - timedelta(minutes=minutes_ago)
    session.flush()
    return request


def _status(session: Session, request_id: UUID) -> str:
    return session.execute(
        select(PickupRequest.status).where(PickupRequest.id == request_id)
    ).scalar_one()


def _overdue_fixture(session: Session) -> dict[str, PickupRequest]:
    return {
        "a": _request(session, minutes_ago=121),
        "b": _request(session, minutes_ago=60, status="acknowledged", reply_source="staff"),
        "c": _request(session, minutes_ago=200, status="completed"),
        "d": _request(session, minutes_ago=30, service_date=_TODAY - timedelta(days=1)),
    }


def test_pickup_expire_overdue(
    db_session: Session, clock: FakeClock, published: list[Call]
) -> None:
    r = _overdue_fixture(db_session)
    arrived = _request(db_session, minutes_ago=180, status="arrived", reply_source="staff")

    expired = expire_overdue_requests(db_session, clock)

    assert expired == 3
    assert {k: _status(db_session, v.id) for k, v in r.items()} == {
        "a": "expired",
        "b": "acknowledged",
        "c": "completed",
        "d": "expired",
    }
    assert _status(db_session, arrived.id) == "expired"
    db_session.commit()
    pushed = {
        m["data"]["id"]: m["data"]["status"]
        for channels, m in published
        if channels == [admin_topic_channel("pickup")]
    }
    assert pushed == {
        str(r["a"].id): "expired",
        str(r["d"].id): "expired",
        str(arrived.id): "expired",
    }


def test_pickup_expire_boundary(db_session: Session, clock: FakeClock) -> None:
    exactly = _request(db_session, minutes_ago=120)

    assert expire_overdue_requests(db_session, clock) == 0
    assert _status(db_session, exactly.id) == "pending"


def test_pickup_expire_setting_change(db_session: Session, clock: FakeClock) -> None:
    r = _overdue_fixture(db_session)
    db_session.execute(
        text(
            "update public.system_settings "
            "set value = jsonb_set(value, '{auto_expire_minutes}', '30'::jsonb) "
            "where key = 'pickup.window'"
        )
    )
    clear_settings_cache()

    assert expire_overdue_requests(db_session, clock) == 3
    assert _status(db_session, r["b"].id) == "expired"
    assert _status(db_session, r["c"].id) == "completed"


def test_pickup_expire_batch_limit(db_session: Session, clock: FakeClock) -> None:
    for _ in range(EXPIRE_BATCH_SIZE + 3):
        _request(db_session, minutes_ago=300)

    assert expire_overdue_requests(db_session, clock) == EXPIRE_BATCH_SIZE
    assert expire_overdue_requests(db_session, clock) == 3
    assert expire_overdue_requests(db_session, clock) == 0


@pytest.fixture
def owner_cleanup_students() -> Iterator[list[UUID]]:
    """committing 測試建立的學生以 owner 連線刪除；排在 committing_db_session 之前
    （先 close session、truncate 請求表，再刪學生）。"""
    ids: list[UUID] = []
    yield ids
    with connect_owner() as conn:
        conn.execute("set lock_timeout = '5s'")
        for student_id in ids:
            conn.execute("delete from public.students where id = %s", (student_id,))
        conn.commit()


@pytest.mark.cleanup_tables("pickup_requests")
def test_pickup_expire_skip_locked(
    owner_cleanup_students: list[UUID],
    committing_db_session: Session,
    db_engine: Engine,
    clock: FakeClock,
) -> None:
    locked = _request(committing_db_session, minutes_ago=121)
    committing_db_session.commit()
    owner_cleanup_students.append(locked.student_id)

    holder = Session(bind=db_engine)
    worker = Session(bind=db_engine)
    try:
        holder.execute(
            text("select id from public.pickup_requests where id = :id for update"),
            {"id": locked.id},
        )
        worker.execute(text("set local statement_timeout = '2s'"))
        assert expire_overdue_requests(worker, clock) == 0
        worker.rollback()
        holder.rollback()

        # 鎖釋放後下一輪就會處理
        assert expire_overdue_requests(worker, clock) == 1
        worker.rollback()
    finally:
        holder.close()
        worker.close()

    assert _status(committing_db_session, locked.id) == "pending"


def test_pickup_expire_registered() -> None:
    spec = JOB_REGISTRY["pickup.expire_requests"]

    assert isinstance(spec.trigger, IntervalTrigger)
    assert spec.trigger.interval == timedelta(minutes=5)
    assert spec.func is expire_overdue_requests
