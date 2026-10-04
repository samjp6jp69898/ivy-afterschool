"""BACKEND-008：app/core/pagination.py（PageParams、Page[T]、paginate）。

不依賴業務表：以 owner 連線建立探針 schema `page_probe_<uuid8>`（只用於建立 / 刪除探針），資料寫入與
查詢一律經 app_backend 的 db_session。
"""

from collections.abc import Iterator
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from psycopg import sql
from sqlalchemy import Column, Integer, MetaData, String, Table, select
from sqlalchemy.orm import Session

from app.core.errors import register_exception_handlers
from app.core.pagination import Page, PageParams, page_params, paginate
from tests.support import db_urls

Conn = psycopg.Connection[tuple[Any, ...]]

SCHEMA = f"page_probe_{uuid4().hex[:8]}"
_metadata = MetaData(schema=SCHEMA)
items = Table(
    "items",
    _metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String, nullable=False),
)
_ROWS = 25


def _owner_connect() -> Conn:
    conn = psycopg.connect(db_urls.to_psycopg_dsn(db_urls.owner_url()), autocommit=True)
    db_urls.assert_connected_loopback(conn.info.hostaddr)
    return conn


@pytest.fixture(scope="module", autouse=True)
def page_probe() -> Iterator[None]:
    schema = sql.Identifier(SCHEMA)
    with _owner_connect() as conn:
        conn.execute(sql.SQL("create schema {}").format(schema))
        conn.execute(
            sql.SQL("create table {}.items (id serial primary key, name text not null)").format(
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


@pytest.fixture
def seeded(db_session: Session) -> Session:
    db_session.execute(items.insert(), [{"name": f"王小明{i:02d}"} for i in range(1, _ROWS + 1)])
    db_session.flush()
    return db_session


# --- PageParams / dependency ------------------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/items")
    def _list(params: PageParams = Depends(page_params)) -> dict[str, int]:  # noqa: B008
        return {"page": params.page, "page_size": params.page_size, "offset": params.offset}

    return TestClient(app)


def test_pagination_params_validation(client: TestClient) -> None:
    too_big = client.get("/items?page_size=201")
    assert too_big.status_code == 422
    assert too_big.json()["error"]["code"] == "validation_error"

    assert client.get("/items?page=0").status_code == 422
    assert client.get("/items?page_size=0").status_code == 422
    assert client.get("/items?page=abc").status_code == 422

    default = client.get("/items")
    assert default.status_code == 200
    assert default.json() == {"page": 1, "page_size": 20, "offset": 0}

    edge = client.get("/items?page=3&page_size=200")
    assert edge.json() == {"page": 3, "page_size": 200, "offset": 400}


def test_pagination_page_model_serializes_items_and_total() -> None:
    page = Page[str](items=["a", "b"], total=7)

    assert page.model_dump() == {"items": ["a", "b"], "total": 7}
    assert PageParams(page=2, page_size=10).offset == 10


# --- paginate ---------------------------------------------------------------------------


def test_pagination_total_and_items(seeded: Session) -> None:
    stmt = select(items.c.name).order_by(items.c.name)

    rows, total = paginate(seeded, stmt, PageParams(page=2, page_size=10))

    assert total == _ROWS
    assert rows == [f"王小明{i:02d}" for i in range(11, 21)]


def test_pagination_total_ignores_limit_and_counts_filtered(seeded: Session) -> None:
    stmt = select(items.c.name).where(items.c.name > "王小明20").order_by(items.c.name.desc())

    rows, total = paginate(seeded, stmt, PageParams(page=1, page_size=3))

    assert total == 5
    assert rows == ["王小明25", "王小明24", "王小明23"]


def test_pagination_out_of_range(seeded: Session) -> None:
    stmt = select(items.c.name).order_by(items.c.name)

    rows, total = paginate(seeded, stmt, PageParams(page=4, page_size=10))

    assert rows == []
    assert total == _ROWS


def test_pagination_requires_order_by(seeded: Session) -> None:
    stmt = select(items.c.name)

    with pytest.raises(ValueError, match="order_by"):
        paginate(seeded, stmt, PageParams(page=1, page_size=10))
