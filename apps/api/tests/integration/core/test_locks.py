"""BACKEND-017：app/core/locks.py（advisory_key、try_advisory_xact_lock、advisory_xact_lock）。

不依賴業務表：advisory lock 只用 Postgres 的 lock 表，被測 session 一律以 app_backend（db_engine）
連線。
"""

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.core.locks import advisory_key, advisory_xact_lock, try_advisory_xact_lock

_NAMESPACE = "job:outbox"


@pytest.fixture
def two_sessions(db_engine: Engine) -> Iterator[tuple[Session, Session]]:
    s1 = Session(bind=db_engine)
    s2 = Session(bind=db_engine)
    try:
        yield s1, s2
    finally:
        for s in (s1, s2):
            s.rollback()
            s.close()


# --- advisory_key -----------------------------------------------------------------------


def test_locks_advisory_key_stable() -> None:
    first = advisory_key(_NAMESPACE, "2026-09-01")

    assert first == advisory_key(_NAMESPACE, "2026-09-01")
    assert first != advisory_key(_NAMESPACE, "2026-09-02")
    assert first != advisory_key("job:other", "2026-09-01")
    assert 0 < first < 2**63
    # md5(b"job:outbox|tick") 前 8 bytes 遮掉最高位：固定輸入固定輸出（換演算法會改變既有鎖的 key）
    assert advisory_key("job:outbox", "tick") == 0x72A38F110B66ADC1


def test_locks_advisory_key_separator_distinct() -> None:
    # namespace 與 key 以 '|' 分隔：('a|b', 'c') 與 ('a', 'b|c') 的 key 不會相同
    assert advisory_key("a:b", "c") != advisory_key("a", "b:c")


# --- try_advisory_xact_lock -------------------------------------------------------------


def test_locks_try_lock_contention(two_sessions: tuple[Session, Session]) -> None:
    s1, s2 = two_sessions

    assert try_advisory_xact_lock(s1, _NAMESPACE, "2026-09-01") is True
    # 同一 session 重入 OK（Postgres advisory lock 可重入）
    assert try_advisory_xact_lock(s1, _NAMESPACE, "2026-09-01") is True
    assert try_advisory_xact_lock(s2, _NAMESPACE, "2026-09-01") is False
    # 不同 key 不互斥
    assert try_advisory_xact_lock(s2, _NAMESPACE, "2026-09-02") is True

    s1.commit()

    assert try_advisory_xact_lock(s2, _NAMESPACE, "2026-09-01") is True


def test_locks_try_lock_released_on_rollback(two_sessions: tuple[Session, Session]) -> None:
    s1, s2 = two_sessions
    assert try_advisory_xact_lock(s1, _NAMESPACE, "x") is True
    assert try_advisory_xact_lock(s2, _NAMESPACE, "x") is False

    s1.rollback()

    assert try_advisory_xact_lock(s2, _NAMESPACE, "x") is True


def test_locks_blocking_lock_holds_until_commit(two_sessions: tuple[Session, Session]) -> None:
    s1, s2 = two_sessions
    advisory_xact_lock(s1, "students:promote_grade", "2026")

    assert try_advisory_xact_lock(s2, "students:promote_grade", "2026") is False
    # 鎖是 transaction 級：pg_locks 看得到一筆 advisory lock 由 s1 持有
    held = s1.execute(
        text(
            "select count(*) from pg_locks "
            "where locktype = 'advisory' and granted and pid = pg_backend_pid()"
        )
    ).scalar_one()
    assert held == 1

    s1.commit()

    assert try_advisory_xact_lock(s2, "students:promote_grade", "2026") is True


# --- namespace 驗證 ---------------------------------------------------------------------


@pytest.mark.parametrize("namespace", ["Job Name", "job:1", "", "job-outbox", "job:outbox\n"])
def test_locks_invalid_namespace(db_engine: Engine, namespace: str) -> None:
    with Session(bind=db_engine) as session:
        with pytest.raises(ValueError, match="namespace"):
            try_advisory_xact_lock(session, namespace, "x")
        with pytest.raises(ValueError, match="namespace"):
            advisory_xact_lock(session, namespace, "x")
        with pytest.raises(ValueError, match="namespace"):
            advisory_key(namespace, "x")
        # 驗證失敗不會開交易
        assert session.in_transaction() is False
