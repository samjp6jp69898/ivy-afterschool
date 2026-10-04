"""endpoint 整合測試的 get_db 替換（BACKEND-005）。

用法（BACKEND-023 的 api_client fixture 以此為準）::

    app.dependency_overrides[get_db] = override_get_db(db_session)

與正式 ``get_db`` 同語意：請求結束一律 rollback，等同 get_db 的「handler 例外時 rollback」與
「close 丟棄未 commit 的變更」。INFRA-010 的 db_session 為 create_savepoint 模式，rollback 只回到
savepoint：先前 commit（釋放 savepoint）的資料與外層交易都保留，測試結束整筆 rollback。

- 不可寫成 ``lambda: db_session``：handler 遇到 DB 錯誤（例如 unique 違反轉 409）後 session 會停在
  aborted，之後的查詢與請求都拋 InFailedSqlTransaction。
- 測試資料建立後先 ``db_session.commit()``（只釋放 savepoint）再打 API；否則請求結束時的 rollback
  會連同未 commit 的測試資料一起退回。
"""

from collections.abc import Callable, Generator

from sqlalchemy.orm import Session


def override_get_db(db_session: Session) -> Callable[[], Generator[Session]]:
    def _get_db() -> Generator[Session]:
        try:
            yield db_session
        finally:
            db_session.rollback()

    return _get_db
