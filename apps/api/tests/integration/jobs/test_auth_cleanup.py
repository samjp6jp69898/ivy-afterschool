"""BACKEND-066：app/jobs/auth_cleanup.py（refresh token 過期 / 撤銷列的清理背景工作）。"""

from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select
from sqlalchemy.orm import Session

import app.jobs  # noqa: F401  觸發 @scheduled_job 註冊
from app.core.scheduler import JOB_REGISTRY, daily_run_key
from app.jobs import auth_cleanup
from app.jobs.auth_cleanup import cleanup_refresh_tokens
from app.models.account import RefreshToken
from tests.support.fake_clock import FakeClock

JOB_ID = "auth.refresh_token_cleanup"


def _token(
    session: Session,
    fake_clock: FakeClock,
    *,
    expires_in: timedelta,
    revoked_ago: timedelta | None = None,
    subject_id: UUID | None = None,
) -> UUID:
    now = fake_clock.now()
    expires_at = now + expires_in
    token = RefreshToken(
        # DB CHECK expires_at > created_at
        created_at=min(now, expires_at - timedelta(days=14)),
        subject_type="staff",
        subject_id=subject_id or uuid4(),
        family_id=uuid4(),
        token_hash=uuid4().hex + uuid4().hex,
        expires_at=expires_at,
        revoked_at=None if revoked_ago is None else now - revoked_ago,
    )
    session.add(token)
    session.flush()
    return token.id


def _remaining_ids(session: Session, subject_id: UUID) -> set[UUID]:
    session.expire_all()
    return set(
        session.execute(
            select(RefreshToken.id).where(RefreshToken.subject_id == subject_id)
        ).scalars()
    )


def test_auth_cleanup_deletes_old(db_session: Session, fake_clock: FakeClock) -> None:
    subject_id = uuid4()
    expired_8d = _token(
        db_session, fake_clock, expires_in=timedelta(days=-8), subject_id=subject_id
    )
    revoked_8d = _token(
        db_session,
        fake_clock,
        expires_in=timedelta(days=10),
        revoked_ago=timedelta(days=8),
        subject_id=subject_id,
    )
    expired_6d = _token(
        db_session, fake_clock, expires_in=timedelta(days=-6), subject_id=subject_id
    )
    alive = _token(db_session, fake_clock, expires_in=timedelta(days=10), subject_id=subject_id)

    assert cleanup_refresh_tokens(db_session, fake_clock) == 2

    assert _remaining_ids(db_session, subject_id) == {expired_6d, alive}
    assert expired_8d not in _remaining_ids(db_session, subject_id)
    assert revoked_8d not in _remaining_ids(db_session, subject_id)
    # 再跑一次沒有可刪的列
    assert cleanup_refresh_tokens(db_session, fake_clock) == 0


def test_auth_cleanup_boundaries(db_session: Session, fake_clock: FakeClock) -> None:
    subject_id = uuid4()
    # 剛好 7 天：expires_at == now - 7d 不小於 cutoff，保留；多 1 秒才刪
    exactly_7d = _token(
        db_session, fake_clock, expires_in=timedelta(days=-7), subject_id=subject_id
    )
    over_7d = _token(
        db_session,
        fake_clock,
        expires_in=timedelta(days=-7, seconds=-1),
        subject_id=subject_id,
    )
    revoked_6d = _token(
        db_session,
        fake_clock,
        expires_in=timedelta(days=10),
        revoked_ago=timedelta(days=6),
        subject_id=subject_id,
    )
    # 撤銷超過 7 天但尚未過期：仍刪
    revoked_old_unexpired = _token(
        db_session,
        fake_clock,
        expires_in=timedelta(days=20),
        revoked_ago=timedelta(days=7, seconds=1),
        subject_id=subject_id,
    )

    assert cleanup_refresh_tokens(db_session, fake_clock) == 2
    assert _remaining_ids(db_session, subject_id) == {exactly_7d, revoked_6d}
    assert over_7d not in _remaining_ids(db_session, subject_id)
    assert revoked_old_unexpired not in _remaining_ids(db_session, subject_id)


def test_auth_cleanup_replaced_by_chain(db_session: Session, fake_clock: FakeClock) -> None:
    """自參照 replaced_by 為 on delete set null：先刪前任或後繼都不影響，留下的列指標清空。"""
    subject_id = uuid4()
    old = _token(db_session, fake_clock, expires_in=timedelta(days=-8), subject_id=subject_id)
    new = _token(db_session, fake_clock, expires_in=timedelta(days=10), subject_id=subject_id)
    db_session.get_one(RefreshToken, old).replaced_by = new
    db_session.flush()

    assert cleanup_refresh_tokens(db_session, fake_clock) == 1

    assert _remaining_ids(db_session, subject_id) == {new}
    assert db_session.get_one(RefreshToken, new).replaced_by is None


def test_auth_cleanup_batch_limit(
    db_session: Session, fake_clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    subject_id = uuid4()
    for _ in range(3):
        _token(db_session, fake_clock, expires_in=timedelta(days=-9), subject_id=subject_id)
    assert auth_cleanup.CLEANUP_BATCH_SIZE == 5000
    monkeypatch.setattr(auth_cleanup, "CLEANUP_BATCH_SIZE", 2)

    assert cleanup_refresh_tokens(db_session, fake_clock) == 2
    assert len(_remaining_ids(db_session, subject_id)) == 1
    # 剩餘的列留待下次執行
    assert cleanup_refresh_tokens(db_session, fake_clock) == 1
    assert _remaining_ids(db_session, subject_id) == set()


def test_auth_cleanup_registered() -> None:
    assert JOB_ID in JOB_REGISTRY
    spec = JOB_REGISTRY[JOB_ID]
    assert isinstance(spec.trigger, CronTrigger)
    fields = {f.name: str(f) for f in spec.trigger.fields}
    assert fields["hour"] == "3"
    assert fields["minute"] == "30"
    assert str(spec.trigger.timezone) == "Asia/Taipei"
    assert spec.run_key is daily_run_key
    assert spec.func is auth_cleanup.run_refresh_token_cleanup
