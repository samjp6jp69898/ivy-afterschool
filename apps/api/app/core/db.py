"""BACKEND-005：SQLAlchemy 2（sync）+ psycopg 3 的 engine、SessionLocal 與 request-scoped get_db。

**交易約定（全後端遵守）**
- service 方法只 ``session.add`` / ``flush``，不 commit；寫入型 endpoint handler 呼叫完 service 後
  自行 ``db.commit()`` 再組回應。
- ``get_db``：handler 拋例外時 rollback；最後一律 close。handler 沒 commit 的變更在 close 時丟棄
  （防止 GET 意外寫入）。
- commit 後才做的事（通知派送、ws 廣播、刪除 Storage 舊檔）一律走 BACKEND-006 的
  ``run_after_commit``，不得在 commit 前呼叫外部服務。
- 連線一律使用 ``app_backend`` 角色（``DATABASE_URL``），不得在程式中 ``set role``。
  URL 含密碼：只交給 SQLAlchemy（Engine 的 repr 會遮罩密碼），不得自行 log。

**測試**
- endpoint 整合測試以 ``tests/support/db_override.py`` 的 ``override_get_db`` 換掉 ``get_db``
  （與本模組 get_db 同語意：請求結束 rollback 到 savepoint；不可用 ``lambda: db_session``，
  DB 錯誤後 session 會停在 aborted）::

      app.dependency_overrides[get_db] = override_get_db(db_session)

- 要驗證 ``get_db`` / ``session_scope`` 本身時，以 ``monkeypatch.setattr(db, "get_engine", ...)``
  換成 ``build_engine(backend_url())``。
"""

from __future__ import annotations

from collections.abc import Generator, Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


def build_engine(database_url: str) -> Engine:
    return create_engine(
        database_url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        pool_recycle=1800,
        connect_args={"options": "-c timezone=UTC"},
    )


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """以 ``get_settings().database_url`` 建立的 module 單例。

    第一次呼叫才建立（import 時不讀 env）；測試以 ``get_engine.cache_clear()`` 重設。
    """
    return build_engine(get_settings().database_url)


# bind 在建立 session 時才由 get_engine() 決定（lazy）
SessionLocal: sessionmaker[Session] = sessionmaker(expire_on_commit=False, autoflush=True)


def get_db() -> Generator[Session]:
    """FastAPI dependency：一個 request 一個 session。"""
    session = SessionLocal(bind=get_engine())
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """給背景工作 / CLI：成功 commit、例外 rollback 後重新拋出、最後 close。"""
    session = SessionLocal(bind=get_engine())
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()
