"""BACKEND-119：參考資料（subjects / exam-types / schools / closed-days）的後台 endpoint。

``build_reference_router(spec)`` 為 ``SPECS`` 的每個資源建一個 router（prefix ``/{resource}``）。
GET 清單：settings:read、students:read、exams:read、homework:read 任一即可（科目、考試類型、國小
是成績 / 學生 / 作業頁的下拉選單來源，課輔老師沒有 settings:read 也需要讀取）。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import CurrentStaff, require_any_permission
from app.core.db import get_db
from app.core.permissions import Permission
from app.schemas.reference import ReferenceListQuery
from app.services import reference_data_service
from app.services.reference_specs import SPECS, ReferenceSpec

READ_PERMISSIONS = (
    Permission.SETTINGS_READ,
    Permission.STUDENTS_READ,
    Permission.EXAMS_READ,
    Permission.HOMEWORK_READ,
)


def build_reference_router(spec: ReferenceSpec) -> APIRouter:
    router = APIRouter(prefix=f"/{spec.resource}", tags=[f"admin-{spec.resource}"])

    @router.get("", response_model=list[spec.out_schema])  # type: ignore[name-defined]
    def list_items(
        _: Annotated[CurrentStaff, Depends(require_any_permission(*READ_PERMISSIONS))],
        query: Annotated[ReferenceListQuery, Query()],
        db: Annotated[Session, Depends(get_db)],
    ) -> list[BaseModel]:
        return reference_data_service.list_items(db, spec, query)

    return router


reference_routers = [build_reference_router(spec) for spec in SPECS.values()]
