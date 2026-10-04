"""BACKEND-006：交易成功 commit 後才執行的掛鉤（通知派送、ws 廣播、Storage 舊檔刪除）。

- ``run_after_commit(session, fn)``：把 fn 排進 ``session.info["after_commit_callbacks"]``（保序）。
  session 尚未開始交易時先 ``begin()``，讓之後的 rollback 一定能清掉清單（after_soft_rollback 只在有
  交易時觸發）。
- ``install_tx_hooks()``：在 ``Session`` class 上註冊 ``after_commit`` / ``after_soft_rollback``
  監聽（重複呼叫冪等）；``create_app`` 與 ``session_scope`` 使用前呼叫。
- ``after_commit``：取出並清空清單後依序執行；單一 callback 拋例外只 ``logger.exception``，不影響
  其他 callback，也不讓已成功的 commit 變成錯誤回應。SQLAlchemy 在 ``begin_nested()`` 的 savepoint
  釋放時也會發 after_commit，此時 ``session.in_nested_transaction()`` 為 True，跳過不執行。
- ``after_soft_rollback``（最外層 rollback）：清空清單不執行；``begin_nested()`` 的 savepoint
  rollback 不清外層的 callback。
- callback 內不得再使用同一個 session 寫 DB（需要 DB 的 callback 自行開 ``session_scope()``）。

**與 INFRA-010 ``db_session``（``join_transaction_mode="create_savepoint"``）搭配的實測結論**：
被測程式碼 ``session.commit()`` 雖然只釋放外層連線的 savepoint，但對 Session 而言是最外層 commit
（不是 ``begin_nested``），``after_commit`` **會觸發**
（``tests/integration/core/test_tx_hooks.py::test_tx_hooks_with_savepoint_session``）。
營運模組的通知測試直接用 ``db_session`` 即可，不必改用 ``committing_db_session``。

移植 ivy ``services/notification/dispatch.py`` 的 ``install_session_hooks`` /
``_drain_after_commit`` / ``_clear_on_rollback`` 概念；改為通用 callback 清單。
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from sqlalchemy import event
from sqlalchemy.orm import Session, SessionTransaction

logger = logging.getLogger(__name__)

CALLBACKS_KEY = "after_commit_callbacks"
_installed = False


def run_after_commit(session: Session, fn: Callable[[], None]) -> None:
    if not session.in_transaction():
        session.begin()
    session.info.setdefault(CALLBACKS_KEY, []).append(fn)


def _drain_after_commit(session: Session) -> None:
    # savepoint（begin_nested）釋放也會觸發 after_commit：等最外層 commit 才執行
    if session.in_nested_transaction():
        return
    callbacks: list[Callable[[], None]] = session.info.pop(CALLBACKS_KEY, [])
    for fn in callbacks:
        try:
            fn()
        except Exception:
            logger.exception("after_commit callback 失敗：%r", fn)


def _clear_on_rollback(session: Session, previous_transaction: SessionTransaction) -> None:
    # savepoint（begin_nested）rollback 只截斷該區段，外層已註冊的 callback 保留
    if previous_transaction.nested:
        return
    session.info.pop(CALLBACKS_KEY, None)


def install_tx_hooks() -> None:
    global _installed
    if _installed:
        return
    event.listen(Session, "after_commit", _drain_after_commit)
    event.listen(Session, "after_soft_rollback", _clear_on_rollback)
    _installed = True
