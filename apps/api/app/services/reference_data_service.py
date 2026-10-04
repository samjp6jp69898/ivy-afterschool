from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError
from app.models.reference import ClosedDay
from app.schemas.reference import ReferenceListQuery
from app.services.reference_specs import ReferenceSpec


def list_items(session: Session, spec: ReferenceSpec, query: ReferenceListQuery) -> list[BaseModel]:
    """不分頁（資料量小）。

    active_only 對 closed-days 不適用；date_from / date_to 含頭尾，只 closed-days 使用。
    """
    stmt = select(spec.model)
    if spec.resource == "closed-days":
        if query.date_from is not None:
            stmt = stmt.where(ClosedDay.date >= query.date_from)
        if query.date_to is not None:
            stmt = stmt.where(ClosedDay.date <= query.date_to)
    elif query.active_only:
        stmt = stmt.where(spec.model.is_active.is_(True))  # type: ignore[attr-defined]
    rows = session.execute(stmt.order_by(*spec.order_by)).scalars().all()
    return [spec.out_schema.model_validate(row) for row in rows]


_UNIQUE_VIOLATION = "23505"


def create_item(session: Session, spec: ReferenceSpec, data: BaseModel) -> BaseModel:
    """在 savepoint 內 insert；撞 unique（名稱不分大小寫去頭尾空白、休息日日期）→ 409。"""
    row = spec.model(**data.model_dump())
    with _translate_unique_conflict(spec), session.begin_nested():
        session.add(row)
        session.flush()
    return spec.out_schema.model_validate(row)


@contextmanager
def _translate_unique_conflict(spec: ReferenceSpec) -> Iterator[None]:
    try:
        yield
    except IntegrityError as exc:
        # 只轉譯 unique 衝突；其他約束違反維持原例外（全域 handler 轉 409 / 記 log）
        if getattr(exc.orig, "sqlstate", None) != _UNIQUE_VIOLATION:
            raise
        subject = "日期" if spec.resource == "closed-days" else "名稱"
        raise ConflictError(spec.conflict_code, f"{subject}已存在") from None


def update_item(session: Session, spec: ReferenceSpec, item_id: UUID, data: BaseModel) -> BaseModel:
    """只更新有給的欄位（exclude_unset，給 null 即清除可空欄位）；不存在 404、名稱衝突 409。"""
    row = session.execute(
        select(spec.model).where(spec.model.id == item_id)  # type: ignore[attr-defined]
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(spec.not_found_code, "找不到資料")
    with _translate_unique_conflict(spec), session.begin_nested():
        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(row, field, value)
        session.flush()
    return spec.out_schema.model_validate(row)
