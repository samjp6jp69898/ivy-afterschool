"""整合測試共用的 SQLAlchemy session fixture（INFRA-010）。

- `db_engine`（session scope）：以 app_backend 連本機 Supabase 的 engine。連線前後都驗 loopback，
  並確認實際角色是不帶 superuser / BYPASSRLS 的 app_backend；任何一項不符就 `pytest.exit`。
  **不提供 fallback**：app_backend 連不上就停止，絕不改用 owner 連線。
- `db_session`：外層 transaction + savepoint 模式。被測程式碼 `session.commit()` 只釋放 savepoint，
  測試結束一律 rollback。
- `committing_db_session`：真 commit 語意（outbox、after-commit 通知），必須搭配
  `@pytest.mark.cleanup_tables("schema.table", ...)`；測試結束以 owner 連線 truncate 所列的表。
  owner 連線只用於清表，被測程式碼拿到的 session 一律是 app_backend。

psycopg 層級的 `backend_conn` / `owner_conn` 等 fixture 由 tests/integration/db/conftest.py
（DB-002）提供。
"""

from collections.abc import Iterator

import psycopg
import pytest
from psycopg import sql
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from tests.support import db_urls

_EXIT_CODE = 2
_BACKEND_ROLE = "app_backend"
_DB_DOWN_MESSAGE = (
    "本機 Supabase 未啟動或 app_backend 無法登入，先跑 just db-start 與 just db-reset --yes"
)
# seed.sql 寫入的表：清空後其他測試與本機開發都會壞
_SEED_TABLES = frozenset({"roles", "system_settings", "subjects", "exam_types"})


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "cleanup_tables(*tables): committing_db_session 結束後以 owner 連線 truncate 的表",
    )


def _verify_backend_connection(engine: Engine) -> None:
    try:
        conn = engine.connect()
    except OperationalError:
        pytest.exit(_DB_DOWN_MESSAGE, returncode=_EXIT_CODE)
    with conn:
        driver_conn = conn.connection.driver_connection
        assert isinstance(driver_conn, psycopg.Connection)
        try:
            db_urls.assert_connected_loopback(driver_conn.info.hostaddr)
        except ValueError as exc:
            pytest.exit(str(exc), returncode=_EXIT_CODE)
        role, is_super, bypass_rls = conn.execute(
            text(
                "select current_user, r.rolsuper, r.rolbypassrls "
                "from pg_roles r where r.rolname = current_user"
            )
        ).one()
    if role != _BACKEND_ROLE or is_super or bypass_rls:
        pytest.exit(
            f"db_session 必須以 app_backend 連線，禁止以 owner 角色繞過 RLS：{role}",
            returncode=_EXIT_CODE,
        )


@pytest.fixture(scope="session")
def db_engine() -> Iterator[Engine]:
    try:
        url = db_urls.backend_url()
    except ValueError as exc:
        pytest.exit(str(exc), returncode=_EXIT_CODE)
    engine = create_engine(url, pool_pre_ping=True)
    try:
        _verify_backend_connection(engine)
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def db_session(db_engine: Engine) -> Iterator[Session]:
    conn = db_engine.connect()
    trans = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        trans.rollback()
        conn.close()


def _table_identifier(name: str) -> sql.Identifier:
    return sql.Identifier(*name.split("."))


def _truncate_as_owner(tables: list[str]) -> None:
    with psycopg.connect(db_urls.to_psycopg_dsn(db_urls.owner_url())) as conn:
        db_urls.assert_connected_loopback(conn.info.hostaddr)
        conn.execute(
            sql.SQL("truncate {} restart identity cascade").format(
                sql.SQL(", ").join(_table_identifier(t) for t in tables)
            )
        )
        conn.commit()


@pytest.fixture
def committing_db_session(request: pytest.FixtureRequest) -> Iterator[Session]:
    marker = request.node.get_closest_marker("cleanup_tables")
    if marker is None or not marker.args:
        pytest.fail("committing_db_session 必須搭配 cleanup_tables marker")
    tables = [str(t) for t in marker.args]
    for table in tables:
        schema, _, name = table.rpartition(".")
        if schema in ("", "public") and name in _SEED_TABLES:
            pytest.fail(f"不可清空 seed 表：{name}")

    # marker 檢查通過才連 DB
    engine: Engine = request.getfixturevalue("db_engine")
    session = Session(bind=engine, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        _truncate_as_owner(tables)
