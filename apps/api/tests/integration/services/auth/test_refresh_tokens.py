"""BACKEND-036：app/services/auth/refresh_tokens.py（issue、hash_refresh）。"""

import hashlib
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models.account import RefreshToken
from app.services.auth.refresh_tokens import hash_refresh, issue
from tests.support.fake_clock import FakeClock


def test_issue_refresh_hash_only(db_session: Session, fake_clock: FakeClock) -> None:
    subject_id = uuid4()

    r = issue(db_session, subject_type="staff", subject_id=subject_id, clock=fake_clock)

    expected_hash = hashlib.sha256(r.raw.encode()).hexdigest()
    assert hash_refresh(r.raw) == expected_hash
    row = db_session.execute(select(RefreshToken).where(RefreshToken.id == r.token_id)).scalar_one()
    assert row.token_hash == expected_hash
    assert row.subject_type == "staff"
    assert row.subject_id == subject_id
    assert row.revoked_at is None
    assert row.replaced_by is None
    # raw 只回給呼叫端，DB 任何欄位都不含明文
    raw_row = db_session.execute(
        text("select * from public.refresh_tokens where id = :id"), {"id": r.token_id}
    ).one()
    assert all(str(value) != r.raw for value in raw_row)
    assert len(r.raw) >= 64


def test_issue_refresh_ttl(db_session: Session, fake_clock: FakeClock) -> None:
    staff = issue(db_session, subject_type="staff", subject_id=uuid4(), clock=fake_clock)
    parent = issue(db_session, subject_type="parent", subject_id=uuid4(), clock=fake_clock)

    rows = {
        row.id: row
        for row in db_session.execute(
            select(RefreshToken).where(RefreshToken.id.in_([staff.token_id, parent.token_id]))
        ).scalars()
    }
    # created_at 以注入時鐘寫入，DB CHECK expires_at > created_at 與時鐘一致
    assert rows[staff.token_id].created_at == fake_clock.now()
    assert rows[staff.token_id].expires_at == fake_clock.now() + timedelta(days=14)
    assert rows[parent.token_id].expires_at == fake_clock.now() + timedelta(days=30)
    assert rows[parent.token_id].subject_type == "parent"


def test_issue_refresh_family(db_session: Session, fake_clock: FakeClock) -> None:
    subject_id = uuid4()

    first = issue(db_session, subject_type="staff", subject_id=subject_id, clock=fake_clock)
    second = issue(db_session, subject_type="staff", subject_id=subject_id, clock=fake_clock)
    assert first.family_id != second.family_id
    assert first.raw != second.raw

    family = uuid4()
    third = issue(
        db_session, subject_type="staff", subject_id=subject_id, clock=fake_clock, family_id=family
    )
    assert third.family_id == family
    stored = db_session.execute(
        select(RefreshToken.family_id).where(RefreshToken.id == third.token_id)
    ).scalar_one()
    assert stored == family
