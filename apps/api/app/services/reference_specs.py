"""BACKEND-114：參考資料四個資源（subjects / exam-types / schools / closed-days）的共用設定。

通用 CRUD service（BACKEND-115 起）依 ``SPECS[resource]`` 取 model、schema、排序與錯誤碼。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final, Literal

from pydantic import BaseModel
from sqlalchemy import ColumnElement

from app.models.base import Base
from app.models.reference import ClosedDay, ExamType, School, Subject
from app.schemas.reference import (
    ClosedDayCreateIn,
    ClosedDayOut,
    ClosedDayUpdateIn,
    NamedItemCreateIn,
    NamedItemOut,
    NamedItemUpdateIn,
    SchoolCreateIn,
    SchoolOut,
    SchoolUpdateIn,
)


@dataclass(frozen=True)
class ReferenceSpec:
    resource: Literal["subjects", "exam-types", "schools", "closed-days"]
    model: type[Base]
    create_schema: type[BaseModel]
    update_schema: type[BaseModel]
    out_schema: type[BaseModel]
    order_by: tuple[ColumnElement[Any], ...]
    not_found_code: str
    conflict_code: str
    # 被 restrict FK 引用時，刪除改為停用（closed_days 不被引用，直接刪除）
    deactivate_when_referenced: bool


SPECS: Final[dict[str, ReferenceSpec]] = {
    "subjects": ReferenceSpec(
        resource="subjects",
        model=Subject,
        create_schema=NamedItemCreateIn,
        update_schema=NamedItemUpdateIn,
        out_schema=NamedItemOut,
        order_by=(Subject.sort_order.expression, Subject.name.expression),
        not_found_code="subject_not_found",
        conflict_code="subject_name_taken",
        deactivate_when_referenced=True,
    ),
    "exam-types": ReferenceSpec(
        resource="exam-types",
        model=ExamType,
        create_schema=NamedItemCreateIn,
        update_schema=NamedItemUpdateIn,
        out_schema=NamedItemOut,
        order_by=(ExamType.sort_order.expression, ExamType.name.expression),
        not_found_code="exam_type_not_found",
        conflict_code="exam_type_name_taken",
        deactivate_when_referenced=True,
    ),
    "schools": ReferenceSpec(
        resource="schools",
        model=School,
        create_schema=SchoolCreateIn,
        update_schema=SchoolUpdateIn,
        out_schema=SchoolOut,
        order_by=(School.name.expression,),
        not_found_code="school_not_found",
        conflict_code="school_name_taken",
        deactivate_when_referenced=True,
    ),
    "closed-days": ReferenceSpec(
        resource="closed-days",
        model=ClosedDay,
        create_schema=ClosedDayCreateIn,
        update_schema=ClosedDayUpdateIn,
        out_schema=ClosedDayOut,
        order_by=(ClosedDay.date.desc(),),
        not_found_code="closed_day_not_found",
        conflict_code="closed_day_exists",
        deactivate_when_referenced=False,
    ),
}
