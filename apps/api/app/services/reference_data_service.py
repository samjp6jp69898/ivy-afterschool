"""BACKEND-115：參考資料（科目 / 考試類型 / 學校 / 休息日）共用 service，依 ReferenceSpec 運作。"""

from __future__ import annotations

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

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
