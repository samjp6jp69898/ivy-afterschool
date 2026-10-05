"""BACKEND-006：交易成功 commit 後才執行的掛鉤（通知派送、ws 廣播、Storage 舊檔刪除）。
BACKEND-545：callback 記錄註冊當下所在的交易層，savepoint rollback 只丟棄該層（含內層）註冊的
callback。

- ``run_after_commit(session, fn)``：把 ``(所在交易, fn)`` 排進
  ``session.info["after_commit_callbacks"]``（保序）。所在交易為目前的 savepoint
  （``get_nested_transaction()``），不在 savepoint 內則為最外層交易（``get_transaction()``）。
  session 尚未開始交易時先 ``begin()``，讓之後的 rollback 一定能清掉清單（after_soft_rollback 只在
  有交易時觸發）。
- ``install_tx_hooks()``：在 ``Session`` class 上註冊 ``after_commit`` / ``after_soft_rollback``
  監聽（重複呼叫冪等）；``create_app`` 與 ``session_scope`` 使用前呼叫。
- ``after_commit``（最外層 commit）：取出並清空清單後依序執行；單一 callback 拋例外只
  ``logger.exception``，不影響其他 callback，也不讓已成功的 commit 變成錯誤回應。SQLAlchemy 在
  ``begin_nested()`` 的 savepoint 釋放時也會發 after_commit，此時
  ``session.in_nested_transaction()`` 為 True，跳過不執行：savepoint 內註冊的 callback 併入外層，
  等最外層 commit 才執行。
- ``after_soft_rollback``：最外層 rollback → 清空清單不執行；savepoint rollback（含
  ``with session.begin_nested():`` 區塊拋例外而回滾）→ 只丟棄在該 savepoint 及其內層 savepoint 中
  註冊的 callback（沿 ``SessionTransaction.parent`` 鏈判定；內層 savepoint 已正常釋放者亦屬其內），
  外層與先前已註冊的保留——否則被回滾的業務寫入（例如 ``NotificationService.enqueue`` 的 outbox
  列）仍會在外層 commit 後 kick / 推播，產生幽靈通知。
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

_Entry = tuple[SessionTransaction, Callable[[], None]]


def _current_transaction(session: Session) -> SessionTransaction:
    nested = session.get_nested_transaction()
    if nested is not None:
        return nested
    transaction = session.get_transaction()
    assert transaction is not None  # noqa: S101  run_after_commit 已確保交易存在
    return transaction


def run_after_commit(session: Session, fn: Callable[[], None]) -> None:
    if not session.in_transaction():
        session.begin()
    entries: list[_Entry] = session.info.setdefault(CALLBACKS_KEY, [])
    entries.append((_current_transaction(session), fn))


def _is_within(transaction: SessionTransaction, ancestor: SessionTransaction) -> bool:
    """transaction 是否為 ancestor 本身或其（任意深度的）內層 savepoint。"""
    current: SessionTransaction | None = transaction
    while current is not None:
        if current is ancestor:
            return True
        current = current.parent
    return False


def _drain_after_commit(session: Session) -> None:
    # savepoint（begin_nested）釋放也會觸發 after_commit：等最外層 commit 才執行
    if session.in_nested_transaction():
        return
    entries: list[_Entry] = session.info.pop(CALLBACKS_KEY, [])
    for _, fn in entries:
        try:
            fn()
        except Exception:
            logger.exception("after_commit callback 失敗：%r", fn)


def _clear_on_rollback(session: Session, previous_transaction: SessionTransaction) -> None:
    if not previous_transaction.nested:
        session.info.pop(CALLBACKS_KEY, None)
        return
    # savepoint rollback：只丟棄在該 savepoint（含其內層）中註冊的 callback，外層的保留
    entries: list[_Entry] | None = session.info.get(CALLBACKS_KEY)
    if entries:
        entries[:] = [(tx, fn) for tx, fn in entries if not _is_within(tx, previous_transaction)]


def install_tx_hooks() -> None:
    global _installed
    if _installed:
        return
    event.listen(Session, "after_commit", _drain_after_commit)
    event.listen(Session, "after_soft_rollback", _clear_on_rollback)
    _installed = True
