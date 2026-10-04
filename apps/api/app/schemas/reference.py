"""BACKEND-114：參考資料（科目 / 考試類型 / 學校 / 休息日）schemas。長度對齊 DB-008~011 CHECK。"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from pydantic import Field

from app.schemas.common import OutModel, RequestModel, UpdateModel


class NamedItemCreateIn(RequestModel):
    name: str = Field(min_length=1, max_length=20)
    sort_order: int = Field(default=0, ge=0)
    is_active: bool = True


class NamedItemUpdateIn(UpdateModel):
    name: str | None = Field(default=None, min_length=1, max_length=20)
    sort_order: int | None = Field(default=None, ge=0)
    is_active: bool | None = None


class NamedItemOut(OutModel):
    id: UUID
    name: str
    sort_order: int
    is_active: bool


class SchoolCreateIn(RequestModel):
    name: str = Field(min_length=1, max_length=50)
    short_name: str | None = Field(default=None, min_length=1, max_length=20)
    is_active: bool = True


class SchoolUpdateIn(UpdateModel):
    nullable_fields = frozenset({"short_name"})

    name: str | None = Field(default=None, min_length=1, max_length=50)
    short_name: str | None = Field(default=None, min_length=1, max_length=20)
    is_active: bool | None = None


class SchoolOut(OutModel):
    id: UUID
    name: str
    short_name: str | None
    is_active: bool


class ClosedDayCreateIn(RequestModel):
    date: date
    reason: str | None = Field(default=None, max_length=100)


class ClosedDayUpdateIn(UpdateModel):
    """date 不可改，要改日期請刪除重建。"""

    nullable_fields = frozenset({"reason"})

    reason: str | None = Field(default=None, max_length=100)


class ClosedDayOut(OutModel):
    id: UUID
    date: date
    reason: str | None


class ReferenceListQuery(RequestModel):
    active_only: bool = False  # closed-days 不適用
    date_from: date | None = None  # 只 closed-days 使用
    date_to: date | None = None


class DeleteResultOut(OutModel):
    deleted: bool
    deactivated: bool
