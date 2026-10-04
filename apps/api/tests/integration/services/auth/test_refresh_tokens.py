"""BACKEND-036：app/services/auth/refresh_tokens.py（issue、hash_refresh）。
BACKEND-037：rotate（輪替、race window、重用偵測撤銷整個 family + token_version +1 並 commit）。
BACKEND-038：revoke_family_by_raw（登出）、revoke_all_for_subject（改密碼 / 停用）。
"""

import hashlib
import logging
from collections.abc import Iterator
from datetime import timedelta
from uuid import UUID, uuid4

import psycopg
import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models.account import RefreshToken, StaffUser
from app.models.parents import ParentAccount
from app.services.auth.refresh_tokens import (
    hash_refresh,
    issue,
    revoke_all_for_subject,
    revoke_family_by_raw,
    rotate,
)
from tests.integration.db.conftest import connect_owner
from tests.support.factories import make_parent, make_staff
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


# --- BACKEND-037 rotate ------------------------------------------------------------------


def _token_row(session: Session, token_id: UUID) -> RefreshToken:
    session.expire_all()
    return session.execute(select(RefreshToken).where(RefreshToken.id == token_id)).scalar_one()


def _family_rows(session: Session, family_id: UUID) -> list[RefreshToken]:
    session.expire_all()
    return list(
        session.execute(select(RefreshToken).where(RefreshToken.family_id == family_id)).scalars()
    )


def _raise_app_error(session: Session, raw: str, clock: FakeClock) -> AppError:
    with pytest.raises(AppError) as info:
        rotate(session, raw, clock=clock)
    return info.value


def test_rotate_refresh_normal(db_session: Session, fake_clock: FakeClock) -> None:
    subject_id = uuid4()
    r1 = issue(db_session, subject_type="staff", subject_id=subject_id, clock=fake_clock)
    fake_clock.advance(minutes=10)

    r2 = rotate(db_session, r1.raw, clock=fake_clock)

    assert r2.raw != r1.raw
    assert r2.family_id == r1.family_id
    assert r2.subject_type == "staff"
    assert r2.subject_id == subject_id
    new_row = db_session.execute(
        select(RefreshToken).where(RefreshToken.token_hash == hash_refresh(r2.raw))
    ).scalar_one()
    assert new_row.family_id == r1.family_id
    assert new_row.subject_id == subject_id
    assert new_row.replaced_by is None
    assert new_row.revoked_at is None
    assert new_row.created_at == fake_clock.now()
    assert new_row.expires_at == fake_clock.now() + timedelta(days=14)
    old_row = _token_row(db_session, r1.token_id)
    assert old_row.replaced_by == new_row.id
    assert old_row.revoked_at is None

    # 新 token 可以再輪替一次（鏈式）
    fake_clock.advance(minutes=10)
    r3 = rotate(db_session, r2.raw, clock=fake_clock)
    assert r3.family_id == r1.family_id
    assert _token_row(db_session, new_row.id).replaced_by is not None


def test_rotate_refresh_invalid(db_session: Session, fake_clock: FakeClock) -> None:
    err = _raise_app_error(db_session, "nope", fake_clock)
    assert (err.status, err.code) == (401, "refresh_invalid")
    assert (
        db_session.execute(
            select(RefreshToken).where(RefreshToken.token_hash == hash_refresh("nope"))
        ).first()
        is None
    )


def test_rotate_refresh_revoked_and_expired(db_session: Session, fake_clock: FakeClock) -> None:
    subject_id = uuid4()
    revoked = issue(db_session, subject_type="staff", subject_id=subject_id, clock=fake_clock)
    db_session.execute(
        text("update public.refresh_tokens set revoked_at = :t where id = :id"),
        {"t": fake_clock.now(), "id": revoked.token_id},
    )
    err = _raise_app_error(db_session, revoked.raw, fake_clock)
    assert (err.status, err.code) == (401, "refresh_revoked")

    staff_token = issue(db_session, subject_type="staff", subject_id=subject_id, clock=fake_clock)
    parent_token = issue(db_session, subject_type="parent", subject_id=uuid4(), clock=fake_clock)
    fake_clock.advance(days=15)
    err = _raise_app_error(db_session, staff_token.raw, fake_clock)
    assert (err.status, err.code) == (401, "refresh_expired")
    # 家長 token TTL 30 天，15 天後仍可輪替
    assert rotate(db_session, parent_token.raw, clock=fake_clock).family_id == (
        parent_token.family_id
    )
    # 過期與撤銷都不產生新列
    assert _token_row(db_session, staff_token.token_id).replaced_by is None
    assert _token_row(db_session, revoked.token_id).replaced_by is None


def test_rotate_refresh_expired_boundary(db_session: Session, fake_clock: FakeClock) -> None:
    r1 = issue(db_session, subject_type="staff", subject_id=uuid4(), clock=fake_clock)
    # expires_at <= now 即過期：剛好到期那一刻不可用
    fake_clock.advance(days=14)
    err = _raise_app_error(db_session, r1.raw, fake_clock)
    assert (err.status, err.code) == (401, "refresh_expired")


def test_rotate_refresh_race_window(db_session: Session, fake_clock: FakeClock) -> None:
    staff = make_staff(db_session)
    r1 = issue(db_session, subject_type="staff", subject_id=staff.id, clock=fake_clock)
    r2 = rotate(db_session, r1.raw, clock=fake_clock)
    fake_clock.advance(seconds=3)

    err = _raise_app_error(db_session, r1.raw, fake_clock)

    assert (err.status, err.code) == (409, "refresh_in_progress")
    rows = _family_rows(db_session, r1.family_id)
    assert len(rows) == 2
    assert all(row.revoked_at is None for row in rows)
    assert _token_row(db_session, r1.token_id).replaced_by is not None
    # 新 token 仍可正常使用，帳號 token_version 不變
    assert (
        db_session.execute(
            select(StaffUser.token_version).where(StaffUser.id == staff.id)
        ).scalar_one()
        == 0
    )
    fake_clock.advance(seconds=3)
    assert rotate(db_session, r2.raw, clock=fake_clock).family_id == r1.family_id

    # 剛好 5 秒仍算 race window
    r4 = issue(db_session, subject_type="staff", subject_id=staff.id, clock=fake_clock)
    rotate(db_session, r4.raw, clock=fake_clock)
    fake_clock.advance(seconds=5)
    err = _raise_app_error(db_session, r4.raw, fake_clock)
    assert err.code == "refresh_in_progress"


@pytest.fixture
def owner_cleanup() -> Iterator[list[tuple[str, UUID]]]:
    """(table, id) 清單；teardown 以 owner 連線逐筆刪除（roles 是 seed 表，不可 truncate）。

    測試參數必須把本 fixture 排在 committing_db_session **之前**：pytest 依參數順序建立、反序
    拆除，session 先 close（釋放未 commit 的列鎖）owner 才刪列；反過來會等鎖。lock_timeout 當保險。
    """
    rows: list[tuple[str, UUID]] = []
    yield rows
    with connect_owner() as conn:
        conn.execute("set lock_timeout = '5s'")
        for table, row_id in rows:
            conn.execute(
                psycopg.sql.SQL("delete from public.{} where id = %s").format(
                    psycopg.sql.Identifier(table)
                ),
                (row_id,),
            )
        conn.commit()


def _count_revoked_via_new_connection(family_id: UUID) -> tuple[int, int]:
    """另開 app_backend 連線查：回 (總筆數, revoked_at 非 null 筆數)，驗證撤銷已 commit。"""
    with psycopg.connect(_backend_dsn()) as conn:
        row = conn.execute(
            "select count(*), count(revoked_at) from public.refresh_tokens where family_id = %s",
            (family_id,),
        ).fetchone()
    assert row is not None
    return int(row[0]), int(row[1])


def _backend_dsn() -> str:
    from tests.support import db_urls

    return db_urls.to_psycopg_dsn(db_urls.backend_url())


def _token_version_via_new_connection(table: str, row_id: UUID) -> int:
    with psycopg.connect(_backend_dsn()) as conn:
        row = conn.execute(
            psycopg.sql.SQL("select token_version from public.{} where id = %s").format(
                psycopg.sql.Identifier(table)
            ),
            (row_id,),
        ).fetchone()
    assert row is not None
    return int(row[0])


@pytest.mark.cleanup_tables("refresh_tokens")
def test_rotate_refresh_reuse_revokes_family(
    owner_cleanup: list[tuple[str, UUID]],
    committing_db_session: Session,
    fake_clock: FakeClock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    session = committing_db_session
    staff = make_staff(session)
    owner_cleanup.append(("staff_users", staff.id))
    owner_cleanup.append(("roles", staff.role_id))
    session.commit()

    r1 = issue(session, subject_type="staff", subject_id=staff.id, clock=fake_clock)
    other = issue(session, subject_type="staff", subject_id=staff.id, clock=fake_clock)
    session.commit()
    r2 = rotate(session, r1.raw, clock=fake_clock)
    session.commit()
    fake_clock.advance(seconds=6)

    with caplog.at_level(logging.WARNING, logger="app.services.auth.refresh_tokens"):
        err = _raise_app_error(session, r1.raw, fake_clock)

    assert (err.status, err.code) == (401, "refresh_reused")
    # 撤銷在 raise 前已 commit：另開連線看得到
    assert _count_revoked_via_new_connection(r1.family_id) == (2, 2)
    assert _token_version_via_new_connection("staff_users", staff.id) == 1
    # 其他 family 不受影響
    assert _count_revoked_via_new_connection(other.family_id) == (1, 0)
    # 撤銷後的新 token 也不能再用
    session.rollback()
    err = _raise_app_error(session, r2.raw, fake_clock)
    assert err.code == "refresh_revoked"
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any(str(staff.id) in m and str(r1.family_id) in m for m in warnings)
    assert all(r1.raw not in m and r2.raw not in m for m in warnings)


@pytest.mark.cleanup_tables("refresh_tokens")
def test_rotate_refresh_reuse_parent_bumps_parent_version(
    owner_cleanup: list[tuple[str, UUID]],
    committing_db_session: Session,
    fake_clock: FakeClock,
) -> None:
    session = committing_db_session
    parent = make_parent(session)
    owner_cleanup.append(("parent_accounts", parent.id))
    # 同 uuid 的員工列：撤銷家長 token 不可誤 bump 員工
    staff = make_staff(session)
    owner_cleanup.append(("staff_users", staff.id))
    owner_cleanup.append(("roles", staff.role_id))
    session.commit()

    r1 = issue(session, subject_type="parent", subject_id=parent.id, clock=fake_clock)
    staff_token = issue(session, subject_type="staff", subject_id=staff.id, clock=fake_clock)
    session.commit()
    rotate(session, r1.raw, clock=fake_clock)
    session.commit()
    fake_clock.advance(seconds=6)

    err = _raise_app_error(session, r1.raw, fake_clock)

    assert (err.status, err.code) == (401, "refresh_reused")
    assert _token_version_via_new_connection("parent_accounts", parent.id) == 1
    assert _token_version_via_new_connection("staff_users", staff.id) == 0
    assert _count_revoked_via_new_connection(r1.family_id) == (2, 2)
    assert _count_revoked_via_new_connection(staff_token.family_id) == (1, 0)
    session.rollback()
    session.expire_all()
    assert (
        session.execute(
            select(ParentAccount.token_version).where(ParentAccount.id == parent.id)
        ).scalar_one()
        == 1
    )


# --- BACKEND-038 revoke ------------------------------------------------------------------


def test_revoke_family_by_raw(db_session: Session, fake_clock: FakeClock) -> None:
    staff = make_staff(db_session)
    f1 = issue(db_session, subject_type="staff", subject_id=staff.id, clock=fake_clock)
    f1_latest = rotate(db_session, f1.raw, clock=fake_clock)
    f2 = issue(db_session, subject_type="staff", subject_id=staff.id, clock=fake_clock)
    fake_clock.advance(minutes=1)

    assert revoke_family_by_raw(db_session, f1_latest.raw, clock=fake_clock) == 2

    f1_rows = _family_rows(db_session, f1.family_id)
    assert len(f1_rows) == 2
    assert all(row.revoked_at == fake_clock.now() for row in f1_rows)
    assert all(row.revoked_at is None for row in _family_rows(db_session, f2.family_id))
    # 登出冪等：不存在的 raw 回 0，已撤銷的 family 再撤一次也回 0
    assert revoke_family_by_raw(db_session, "nope", clock=fake_clock) == 0
    assert revoke_family_by_raw(db_session, f1_latest.raw, clock=fake_clock) == 0
    assert revoke_family_by_raw(db_session, f1.raw, clock=fake_clock) == 0
    # 撤銷後整個 family 都不能再輪替
    err = _raise_app_error(db_session, f1_latest.raw, fake_clock)
    assert err.code == "refresh_revoked"
    # 帳號 token_version 不變（登出單一裝置不踢其他裝置的 access token）
    db_session.expire_all()
    assert (
        db_session.execute(
            select(StaffUser.token_version).where(StaffUser.id == staff.id)
        ).scalar_one()
        == 0
    )


def test_revoke_all_for_subject(db_session: Session, fake_clock: FakeClock) -> None:
    staff_a = make_staff(db_session)
    staff_b = make_staff(db_session)
    a1 = issue(db_session, subject_type="staff", subject_id=staff_a.id, clock=fake_clock)
    rotate(db_session, a1.raw, clock=fake_clock)
    a2 = issue(db_session, subject_type="staff", subject_id=staff_a.id, clock=fake_clock)
    b1 = issue(db_session, subject_type="staff", subject_id=staff_b.id, clock=fake_clock)
    # 同 uuid 但 subject_type 不同的列：不可被員工的撤銷波及
    same_uuid_parent = issue(
        db_session, subject_type="parent", subject_id=staff_a.id, clock=fake_clock
    )
    fake_clock.advance(minutes=1)

    revoked = revoke_all_for_subject(
        db_session, subject_type="staff", subject_id=staff_a.id, clock=fake_clock
    )

    assert revoked == 3
    for family_id in (a1.family_id, a2.family_id):
        assert all(
            row.revoked_at == fake_clock.now() for row in _family_rows(db_session, family_id)
        )
    assert all(row.revoked_at is None for row in _family_rows(db_session, b1.family_id))
    assert _token_row(db_session, same_uuid_parent.token_id).revoked_at is None
    # 家長側以同 uuid 撤銷只影響家長列
    assert (
        revoke_all_for_subject(
            db_session, subject_type="parent", subject_id=staff_a.id, clock=fake_clock
        )
        == 1
    )
    assert _token_row(db_session, same_uuid_parent.token_id).revoked_at == fake_clock.now()
    assert all(row.revoked_at is None for row in _family_rows(db_session, b1.family_id))


def test_revoke_all_idempotent_count(db_session: Session, fake_clock: FakeClock) -> None:
    staff = make_staff(db_session)
    issue(db_session, subject_type="staff", subject_id=staff.id, clock=fake_clock)
    issue(db_session, subject_type="staff", subject_id=staff.id, clock=fake_clock)

    first = revoke_all_for_subject(
        db_session, subject_type="staff", subject_id=staff.id, clock=fake_clock
    )
    first_revoked_at = fake_clock.now()
    fake_clock.advance(minutes=5)
    second = revoke_all_for_subject(
        db_session, subject_type="staff", subject_id=staff.id, clock=fake_clock
    )

    assert (first, second) == (2, 0)
    # 第二次不得覆寫原本的 revoked_at
    db_session.expire_all()
    rows = db_session.execute(
        select(RefreshToken.revoked_at).where(RefreshToken.subject_id == staff.id)
    ).scalars()
    assert set(rows) == {first_revoked_at}
    # 沒有任何 token 的 subject 回 0
    assert (
        revoke_all_for_subject(
            db_session, subject_type="staff", subject_id=uuid4(), clock=fake_clock
        )
        == 0
    )
