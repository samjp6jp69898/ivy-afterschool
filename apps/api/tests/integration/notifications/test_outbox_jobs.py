"""BACKEND-209：app/notifications/outbox_jobs.py（commit 後 kick_outbox 與每 30 秒 sweep）。

kick 走自己的 session_scope（以 db_engine 取代 get_engine）；sweep 經 BACKEND-018 run_job_once。
測資以 committing session 建立並 commit，另開連線驗證。
"""

import logging
import threading
from collections.abc import Iterator
from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

import app.jobs  # noqa: F401  觸發 @scheduled_job 註冊
from app.core import db as core_db
from app.core.config import get_settings
from app.core.crypto import derive_key
from app.core.scheduler import JOB_REGISTRY, run_job_once
from app.models.notifications import Notification, NotificationOutbox
from app.models.parents import ParentAccount
from app.notifications import outbox_jobs
from app.notifications.events import Event
from app.notifications.outbox_jobs import JOB_ID, kick_outbox, set_kick_mode, sweep_outbox
from app.notifications.templates import render
from tests.integration.db.conftest import connect_owner
from tests.support.factories import make_parent
from tests.support.fake_clock import FakeClock
from tests.support.fake_line import FakeLineMessagingClient

_PAYLOAD: dict[str, Any] = {"student_name": "王小明", "student_id": "x"}


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, db_engine: Engine) -> Iterator[None]:
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
    # kick 的 session_scope 用測試 engine（app_backend）
    monkeypatch.setattr(core_db, "get_engine", lambda: db_engine)
    outbox_jobs.reset_line_client_cache()
    yield
    set_kick_mode("thread")
    outbox_jobs.reset_line_client_cache()
    get_settings.cache_clear()
    derive_key.cache_clear()


@pytest.fixture
def owner_delete_parents() -> Iterator[list[UUID]]:
    ids: list[UUID] = []
    yield ids
    with connect_owner() as conn:
        conn.execute("set lock_timeout = '5s'")
        for parent_id in ids:
            conn.execute("delete from public.parent_accounts where id = %s", (parent_id,))
        conn.commit()


def _committed_outbox(
    session: Session, fake_clock: FakeClock, parents: list[UUID]
) -> tuple[ParentAccount, UUID]:
    parent = make_parent(session)
    rendered = render(Event.HOMEWORK_DONE, _PAYLOAD)
    notification = Notification(
        recipient_type="parent",
        recipient_id=parent.id,
        event=Event.HOMEWORK_DONE.value,
        title=rendered.title,
        body=rendered.body,
        payload=_PAYLOAD,
    )
    session.add(notification)
    session.flush()
    outbox = NotificationOutbox(notification_id=notification.id, next_attempt_at=fake_clock.now())
    session.add(outbox)
    session.commit()
    parents.append(parent.id)
    return parent, outbox.id


def _status(db_engine: Engine, outbox_id: UUID) -> str:
    with Session(bind=db_engine) as s:
        return str(
            s.execute(
                select(NotificationOutbox.status).where(NotificationOutbox.id == outbox_id)
            ).scalar_one()
        )


@pytest.mark.cleanup_tables("notification_outbox", "notifications")
def test_outbox_kick_sync(
    owner_delete_parents: list[UUID],
    committing_db_session: Session,
    db_engine: Engine,
    fake_clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent, outbox_id = _committed_outbox(committing_db_session, fake_clock, owner_delete_parents)
    fake = FakeLineMessagingClient()
    monkeypatch.setattr(outbox_jobs, "build_line_client", lambda session: fake)
    monkeypatch.setattr(outbox_jobs, "get_clock", lambda: fake_clock)
    set_kick_mode("sync")

    kick_outbox([outbox_id])

    assert _status(db_engine, outbox_id) == "sent"
    assert [(c[0], c[2]) for c in fake.calls] == [(parent.line_user_id, outbox_id)]
    # 空清單不做事
    kick_outbox([])
    assert len(fake.calls) == 1


@pytest.mark.cleanup_tables("notification_outbox", "notifications")
def test_outbox_kick_off_then_sweep(
    owner_delete_parents: list[UUID],
    committing_db_session: Session,
    db_engine: Engine,
    fake_clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, outbox_id = _committed_outbox(committing_db_session, fake_clock, owner_delete_parents)
    fake = FakeLineMessagingClient()
    monkeypatch.setattr(outbox_jobs, "build_line_client", lambda session: fake)
    set_kick_mode("off")

    kick_outbox([outbox_id])
    assert _status(db_engine, outbox_id) == "pending"
    assert fake.calls == []

    result = run_job_once(
        JOB_REGISTRY[JOB_ID], session_factory=lambda: Session(bind=db_engine), clock=fake_clock
    )

    assert result == "ran"
    assert _status(db_engine, outbox_id) == "sent"
    assert len(fake.calls) == 1


def test_outbox_sweep_does_not_commit(
    db_session: Session, fake_clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """sweep 只 flush：advisory lock 是交易級，由 runner commit（BACKEND-018）。"""
    commits: list[bool] = []
    monkeypatch.setattr(outbox_jobs, "build_line_client", lambda session: None)
    original_commit = db_session.commit

    def spying_commit() -> None:
        commits.append(True)
        original_commit()

    monkeypatch.setattr(db_session, "commit", spying_commit)

    sweep_outbox(db_session, fake_clock)

    assert commits == []


def test_outbox_kick_swallows_errors(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("line down")

    monkeypatch.setattr(outbox_jobs, "dispatch_due", boom)
    set_kick_mode("sync")

    with caplog.at_level(logging.ERROR, logger="app.notifications.outbox_jobs"):
        kick_outbox([UUID(int=1)])

    assert any(
        "RuntimeError" in (r.exc_text or "") or "RuntimeError" in r.getMessage()
        for r in caplog.records
    )


def test_outbox_kick_thread_mode_runs_in_background(monkeypatch: pytest.MonkeyPatch) -> None:
    done = threading.Event()
    seen: list[list[UUID]] = []

    def record(ids: list[UUID]) -> None:
        seen.append(list(ids))
        done.set()

    monkeypatch.setattr(outbox_jobs, "_run_kick", record)
    set_kick_mode("thread")

    kick_outbox([UUID(int=7)])

    assert done.wait(timeout=5)
    assert seen == [[UUID(int=7)]]


def test_outbox_sweep_registered() -> None:
    spec = JOB_REGISTRY[JOB_ID]
    assert JOB_ID == "notifications.outbox_sweep"
    assert isinstance(spec.trigger, IntervalTrigger)
    assert spec.trigger.interval == timedelta(seconds=30)
    assert spec.func is sweep_outbox


def test_outbox_set_kick_mode_validates() -> None:
    with pytest.raises(ValueError, match="kick mode"):
        set_kick_mode("async")  # type: ignore[arg-type]
