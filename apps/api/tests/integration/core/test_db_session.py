"""BACKEND-005：app/core/db.py（engine、SessionLocal、get_db、session_scope）。

不依賴業務表：以 owner 連線建立探針 schema `db_probe_<uuid8>`（只用於建立 / 刪除探針與驗證
結果），被測的 engine 一律以 app_backend 連線（tests.support.db_urls.backend_url()）。
"""

from collections.abc import Iterator
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from psycopg import sql
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.core import db
from app.core.config import get_settings
from app.core.db import build_engine, get_db, session_scope
from app.core.errors import register_exception_handlers
from tests.support import db_urls
from tests.support.db_override import override_get_db

Conn = psycopg.Connection[tuple[Any, ...]]

SCHEMA = f"db_probe_{uuid4().hex[:8]}"
TABLE = f"{SCHEMA}.items"


def _owner_connect() -> Conn:
    conn = psycopg.connect(db_urls.to_psycopg_dsn(db_urls.owner_url()), autocommit=True)
    db_urls.assert_connected_loopback(conn.info.hostaddr)
    return conn


def _count() -> int:
    with _owner_connect() as conn:
        row = conn.execute(
            sql.SQL("select count(*) from {}.items").format(sql.Identifier(SCHEMA))
        ).fetchone()
    assert row is not None
    return int(row[0])


@pytest.fixture(scope="module", autouse=True)
def db_probe() -> Iterator[None]:
    schema = sql.Identifier(SCHEMA)
    with _owner_connect() as conn:
        conn.execute(sql.SQL("create schema {}").format(schema))
        conn.execute(
            sql.SQL("create table {}.items (id serial primary key, name text unique)").format(
                schema
            )
        )
        conn.execute(sql.SQL("grant usage on schema {} to app_backend").format(schema))
        conn.execute(sql.SQL("grant all on {}.items to app_backend").format(schema))
        conn.execute(
            sql.SQL("grant usage on sequence {}.items_id_seq to app_backend").format(schema)
        )
    yield
    with _owner_connect() as conn:
        conn.execute(sql.SQL("drop schema {} cascade").format(schema))


@pytest.fixture(autouse=True)
def clean_items() -> Iterator[None]:
    yield
    with _owner_connect() as conn:
        conn.execute(sql.SQL("truncate {}.items").format(sql.Identifier(SCHEMA)))


@pytest.fixture
def backend_engine(monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    """build_engine(backend_url())，並讓 get_db / session_scope 取得這個 engine。"""
    engine = build_engine(db_urls.backend_url())
    monkeypatch.setattr(db, "get_engine", lambda: engine)
    yield engine
    engine.dispose()


def _insert(session: Session, name: str = "王小明") -> None:
    session.execute(text(f"insert into {TABLE} (name) values (:name)"), {"name": name})  # noqa: S608


def _app(handler_mode: str) -> TestClient:
    app = FastAPI()

    @app.post("/items")
    def create_item(session: Session = Depends(get_db)) -> dict[str, str]:  # noqa: B008
        _insert(session)
        if handler_mode == "raise":
            raise RuntimeError("boom")
        if handler_mode == "commit":
            session.commit()
        return {"ok": handler_mode}

    return TestClient(app, raise_server_exceptions=False)


# --- get_db -------------------------------------------------------------------------------


@pytest.mark.usefixtures("backend_engine")
def test_db_get_db_rollback_on_error() -> None:
    response = _app("raise").post("/items")

    assert response.status_code == 500
    assert _count() == 0


@pytest.mark.usefixtures("backend_engine")
def test_db_get_db_discards_uncommitted() -> None:
    response = _app("no-commit").post("/items")

    assert response.status_code == 200
    assert _count() == 0


@pytest.mark.usefixtures("backend_engine")
def test_db_get_db_commit_persists() -> None:
    response = _app("commit").post("/items")

    assert response.status_code == 200
    assert _count() == 1


def test_db_get_db_closes_session(backend_engine: Engine) -> None:
    gen = get_db()
    session = next(gen)
    _insert(session)
    with pytest.raises(StopIteration):
        next(gen)

    # close 後連線已歸還 pool，未 commit 的寫入不落地
    assert backend_engine.pool.checkedout() == 0  # type: ignore[attr-defined]
    assert _count() == 0


def test_db_get_db_rolls_back_when_handler_raises(backend_engine: Engine) -> None:
    gen = get_db()
    session = next(gen)
    _insert(session)
    session.flush()

    with pytest.raises(ValueError, match="handler"):
        gen.throw(ValueError("handler failed"))

    assert backend_engine.pool.checkedout() == 0  # type: ignore[attr-defined]
    assert _count() == 0


# --- session_scope ------------------------------------------------------------------------


@pytest.mark.usefixtures("backend_engine")
def test_db_session_scope_commit_and_rollback() -> None:
    with session_scope() as session:
        _insert(session, "林小華")
    assert _count() == 1

    def failing_job() -> None:
        with session_scope() as session:
            _insert(session, "陳小美")
            raise ValueError("中途失敗")

    with pytest.raises(ValueError, match="中途失敗"):
        failing_job()
    assert _count() == 1


# --- engine -------------------------------------------------------------------------------


def test_db_timezone_utc(backend_engine: Engine) -> None:
    with Session(backend_engine) as session:
        assert session.execute(text("show timezone")).scalar() == "UTC"
        # 由連線參數設定（source == client），不依賴伺服器的預設時區剛好是 UTC
        source = session.execute(
            text("select source from pg_settings where name = 'TimeZone'")
        ).scalar()
        assert source == "client"
        assert session.execute(text("select current_user")).scalar() == "app_backend"


def test_db_build_engine_pool_settings() -> None:
    engine = build_engine(db_urls.backend_url())
    try:
        pool: Any = engine.pool
        assert pool.size() == 5
        assert pool._max_overflow == 5
        assert pool._recycle == 1800
        assert pool._pre_ping is True
        # repr 不含密碼
        assert "app_backend_local" not in repr(engine)
        assert "app_backend_local" not in str(engine.url)
    finally:
        engine.dispose()


def test_db_session_local_settings(backend_engine: Engine) -> None:
    session = db.SessionLocal(bind=backend_engine)
    try:
        assert session.expire_on_commit is False
        assert session.autoflush is True
    finally:
        session.close()


def test_db_get_engine_uses_settings_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", db_urls.backend_url())
    monkeypatch.setenv("APP_SECRET_KEY", "s" * 48)
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://127.0.0.1:5341")
    monkeypatch.setenv("R2_ENDPOINT_URL", "http://127.0.0.1:54344")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "afterschool")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "afterschool-local-secret")
    monkeypatch.setenv("R2_BUCKET", "afterschool-local")
    get_settings.cache_clear()
    db.get_engine.cache_clear()
    try:
        engine = db.get_engine()
        assert db.get_engine() is engine
        with engine.connect() as conn:
            assert conn.execute(text("select current_user")).scalar() == "app_backend"
    finally:
        db.get_engine.cache_clear()
        get_settings.cache_clear()


# --- endpoint 測試的 override 寫法 ----------------------------------------------------------


def test_db_get_db_override_with_db_session(db_session: Session) -> None:
    """之後所有 endpoint 整合測試照這個寫法：get_db 換成 override_get_db(db_session)。

    handler 的 commit 只釋放 savepoint；測試結束 db_session 整筆 rollback，不留資料。
    """
    app = FastAPI()

    @app.post("/items")
    def create_item(session: Session = Depends(get_db)) -> dict[str, int]:  # noqa: B008
        _insert(session)
        session.commit()
        return {"count": int(session.execute(text(f"select count(*) from {TABLE}")).scalar_one())}  # noqa: S608

    app.dependency_overrides[get_db] = override_get_db(db_session)
    response = TestClient(app).post("/items")

    assert response.status_code == 200
    assert response.json() == {"count": 1}
    assert db_session.execute(text(f"select count(*) from {TABLE}")).scalar_one() == 1  # noqa: S608
    # 外層交易尚未結束：其他連線看不到
    assert _count() == 0


def _count_in(session: Session) -> int:
    return int(session.execute(text(f"select count(*) from {TABLE}")).scalar_one())  # noqa: S608


def _override_app(db_session: Session) -> TestClient:
    """與正式 app 相同：IntegrityError 由 BACKEND-003 的 handler 轉成 409。"""
    app = FastAPI()
    register_exception_handlers(app)

    @app.post("/items/{name}")
    def create_item(name: str, session: Session = Depends(get_db)) -> dict[str, str]:  # noqa: B008
        _insert(session, name)
        session.commit()
        return {"name": name}

    @app.post("/draft/{name}")
    def draft_item(name: str, session: Session = Depends(get_db)) -> dict[str, str]:  # noqa: B008
        _insert(session, name)
        session.flush()
        return {"name": name}  # 刻意不 commit

    app.dependency_overrides[get_db] = override_get_db(db_session)
    return TestClient(app, raise_server_exceptions=False)


def test_db_get_db_override_rolls_back_on_handler_error(db_session: Session) -> None:
    _insert(db_session, "王小明")
    db_session.commit()  # 只釋放 savepoint：測試資料先 commit 再打 API
    client = _override_app(db_session)

    assert client.post("/items/林小華").status_code == 200
    conflict = client.post("/items/王小明")
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "conflict"

    # db_session 沒有停在 aborted：仍可查詢，筆數是成功的那些
    assert _count_in(db_session) == 2
    assert client.post("/items/王小明").status_code == 409
    assert client.post("/items/陳小美").status_code == 200
    assert _count_in(db_session) == 3
    # 外層交易尚未結束：其他連線看不到
    assert _count() == 0


def test_db_get_db_override_first_request_fails_keeps_committed_fixture(
    db_session: Session,
) -> None:
    _insert(db_session, "王小明")
    db_session.commit()
    client = _override_app(db_session)

    assert client.post("/items/王小明").status_code == 409

    assert _count_in(db_session) == 1


def test_db_get_db_override_discards_uncommitted(db_session: Session) -> None:
    client = _override_app(db_session)

    response = client.post("/draft/林小華")

    assert response.status_code == 200
    # 與 get_db 的 close 同語意：handler 沒 commit 的寫入在請求結束後不存在
    assert _count_in(db_session) == 0
    assert client.post("/items/林小華").status_code == 200
    assert _count_in(db_session) == 1
