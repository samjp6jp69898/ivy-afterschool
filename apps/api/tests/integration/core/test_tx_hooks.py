"""BACKEND-006：app/core/tx_hooks.py（run_after_commit / install_tx_hooks）。

不依賴業務表：以 owner 連線建立探針 schema `tx_probe`（committing_db_session 的 cleanup_tables 需要
一張可清的表），被測 session 一律是 app_backend。
"""

import contextlib
import logging
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.tx_hooks import CALLBACKS_KEY, install_tx_hooks, run_after_commit
from tests.support import db_urls

Conn = psycopg.Connection[tuple[Any, ...]]

SCHEMA = "tx_probe"
TABLE = f"{SCHEMA}.items"


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
def tx_probe() -> Iterator[None]:
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
def hooks() -> None:
    install_tx_hooks()
    install_tx_hooks()  # 重複呼叫冪等：callback 不會跑兩次


def _insert(session: Session, name: str = "王小明") -> None:
    session.execute(text(f"insert into {TABLE} (name) values (:name)"), {"name": name})  # noqa: S608


def _appender(calls: list[str], name: str) -> Any:
    def _fn() -> None:
        calls.append(name)

    return _fn


# --- committing_db_session：真 commit ------------------------------------------------------


@pytest.mark.cleanup_tables(TABLE)
def test_tx_hooks_run_after_commit_in_order(committing_db_session: Session) -> None:
    calls: list[str] = []
    _insert(committing_db_session)
    run_after_commit(committing_db_session, _appender(calls, "fn1"))
    run_after_commit(committing_db_session, _appender(calls, "fn2"))
    assert committing_db_session.info[CALLBACKS_KEY] != []

    committing_db_session.flush()
    assert calls == []

    committing_db_session.commit()

    assert calls == ["fn1", "fn2"]
    assert _count() == 1
    assert committing_db_session.info.get(CALLBACKS_KEY, []) == []
    # 清單已清空：下一次 commit 不會補跑
    committing_db_session.commit()
    assert calls == ["fn1", "fn2"]


@pytest.mark.cleanup_tables(TABLE)
def test_tx_hooks_discard_on_rollback(committing_db_session: Session) -> None:
    calls: list[str] = []
    _insert(committing_db_session)
    run_after_commit(committing_db_session, _appender(calls, "fn"))

    committing_db_session.rollback()
    assert calls == []
    assert committing_db_session.info.get(CALLBACKS_KEY, []) == []

    _insert(committing_db_session, "林小華")
    committing_db_session.commit()

    assert calls == []
    assert _count() == 1


@pytest.mark.cleanup_tables(TABLE)
def test_tx_hooks_callback_error_isolated(
    committing_db_session: Session, caplog: pytest.LogCaptureFixture
) -> None:
    calls: list[str] = []

    def boom() -> None:
        raise RuntimeError("callback 爆炸")

    _insert(committing_db_session)
    run_after_commit(committing_db_session, boom)
    run_after_commit(committing_db_session, _appender(calls, "fn2"))

    with caplog.at_level(logging.ERROR):
        committing_db_session.commit()

    assert calls == ["fn2"]
    assert _count() == 1
    assert "RuntimeError" in caplog.text
    assert "callback 爆炸" in caplog.text


@pytest.mark.cleanup_tables(TABLE)
def test_tx_hooks_nested_savepoint_does_not_fire(committing_db_session: Session) -> None:
    """begin_nested 的 savepoint 釋放不算 commit；rollback savepoint 也不清掉外層的 callback。"""
    calls: list[str] = []
    run_after_commit(committing_db_session, _appender(calls, "outer"))

    with committing_db_session.begin_nested():
        _insert(committing_db_session)
    assert calls == []

    nested = committing_db_session.begin_nested()
    _insert(committing_db_session, "林小華")
    nested.rollback()
    assert committing_db_session.info[CALLBACKS_KEY] != []

    committing_db_session.commit()

    assert calls == ["outer"]
    assert _count() == 1


# --- INFRA-010 db_session（create_savepoint 模式）--------------------------------------------


def test_tx_hooks_with_savepoint_session(db_session: Session) -> None:
    """db_session.commit() 只釋放 savepoint，但對 Session 而言是最外層 commit：after_commit 會觸發。

    結論與 app/core/tx_hooks.py docstring 一致：營運模組的通知測試可以直接用 db_session。
    """
    calls: list[str] = []
    _insert(db_session)
    run_after_commit(db_session, _appender(calls, "fn"))

    db_session.commit()

    assert calls == ["fn"]
    # 外層交易尚未結束：其他連線看不到資料
    assert _count() == 0


def test_tx_hooks_with_savepoint_session_rollback(db_session: Session) -> None:
    calls: list[str] = []
    run_after_commit(db_session, _appender(calls, "fn"))

    db_session.rollback()
    db_session.commit()

    assert calls == []


# --- BACKEND-545：savepoint rollback 丟棄該層註冊的 callback -------------------------------------


@pytest.mark.cleanup_tables(TABLE)
def test_tx_hooks_savepoint_rollback_discards_inner(committing_db_session: Session) -> None:
    calls: list[str] = []
    _insert(committing_db_session)
    run_after_commit(committing_db_session, _appender(calls, "fn_outer"))

    # begin_nested 區塊內拋例外 → 該 savepoint 回滾（業務錯誤的典型路徑）
    with contextlib.suppress(RuntimeError), committing_db_session.begin_nested():
        _insert(committing_db_session, "林小華")
        run_after_commit(committing_db_session, _appender(calls, "fn_inner"))
        raise RuntimeError("業務錯誤，savepoint 回滾")
    assert calls == []

    committing_db_session.commit()

    assert calls == ["fn_outer"]
    assert _count() == 1


@pytest.mark.cleanup_tables(TABLE)
def test_tx_hooks_savepoint_release_keeps_inner(committing_db_session: Session) -> None:
    calls: list[str] = []
    with committing_db_session.begin_nested():
        _insert(committing_db_session)
        run_after_commit(committing_db_session, _appender(calls, "fn_inner"))
    assert calls == []

    committing_db_session.commit()

    assert calls == ["fn_inner"]
    assert _count() == 1


@pytest.mark.cleanup_tables(TABLE)
def test_tx_hooks_nested_savepoint_rollback(committing_db_session: Session) -> None:
    calls: list[str] = []
    with committing_db_session.begin_nested():
        run_after_commit(committing_db_session, _appender(calls, "fn_a"))
        savepoint_b = committing_db_session.begin_nested()
        run_after_commit(committing_db_session, _appender(calls, "fn_b"))
        savepoint_b.rollback()
    # 已釋放的 savepoint A 若之後整層被回滾（A 的外層再回滾）不在此案例；這裡 A 正常釋放
    assert calls == []

    committing_db_session.commit()

    assert calls == ["fn_a"]

    # 反向：內層 B 正常釋放、外層 A 回滾 → fn_a、fn_b 都丟棄
    calls.clear()
    savepoint_a = committing_db_session.begin_nested()
    run_after_commit(committing_db_session, _appender(calls, "fn_a2"))
    with committing_db_session.begin_nested():
        run_after_commit(committing_db_session, _appender(calls, "fn_b2"))
    savepoint_a.rollback()
    run_after_commit(committing_db_session, _appender(calls, "fn_after"))
    committing_db_session.commit()
    assert calls == ["fn_after"]


def test_tx_hooks_savepoint_rollback_with_db_session(db_session: Session) -> None:
    """INFRA-010 db_session 本身是 savepoint 模式：測試內再開 begin_nested() 構造內層 savepoint。"""
    calls: list[str] = []
    run_after_commit(db_session, _appender(calls, "outer"))
    nested = db_session.begin_nested()
    run_after_commit(db_session, _appender(calls, "inner"))
    nested.rollback()

    db_session.commit()

    assert calls == ["outer"]
