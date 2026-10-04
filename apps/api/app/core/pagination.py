"""BACKEND-008：分頁（domain_spec §2：``?page=&page_size=``，回 ``{"items": [...], "total": n}``）。

- ``page_params``：FastAPI dependency，``page >= 1``、``1 <= page_size <= 200``（預設 1 / 20），
  違反 → 422 validation_error（BACKEND-003）。
- ``paginate(session, stmt, params)``：total 以
  ``select(count()).select_from(stmt.order_by(None).subquery())`` 計算（不受 limit / offset 影響），
  items 以 ``stmt.limit(page_size).offset(offset)`` 取；stmt 沒有 order_by 時拒絕（分頁順序才
  穩定）。超出範圍的 page 回空 items、total 照實。``stmt`` 型別為 ``Select[M]``（SQLAlchemy 2.1 的
  variadic Select，單一 entity / 欄位）。

移植 ivy ``utils/pagination.py::paginate`` 的 count / offset 作法；去掉 legacy cap。
"""

from __future__ import annotations

from typing import Annotated, TypeVar

from fastapi import Query
from pydantic import BaseModel
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 200

T = TypeVar("T")


class PageParams(BaseModel):
    page: int
    page_size: int

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


class Page[T](BaseModel):
    items: list[T]
    total: int


def page_params(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
) -> PageParams:
    return PageParams(page=page, page_size=page_size)


def paginate[M](session: Session, stmt: Select[M], params: PageParams) -> tuple[list[M], int]:
    if not stmt._order_by_clauses:
        raise ValueError("paginate 需要明確的 order_by")
    count_stmt: Select[int] = select(func.count()).select_from(stmt.order_by(None).subquery())
    total = session.execute(count_stmt).scalar_one()
    rows = session.execute(stmt.limit(params.page_size).offset(params.offset)).scalars()
    return list(rows), total
