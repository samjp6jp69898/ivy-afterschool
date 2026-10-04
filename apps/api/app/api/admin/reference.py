"""BACKEND-119：參考資料（subjects / exam-types / schools / closed-days）的後台 endpoint。

``build_reference_router(spec)`` 為 ``SPECS`` 的每個資源建一個 router（prefix ``/{resource}``）。
GET 清單：settings:read、students:read、exams:read、homework:read 任一即可（科目、考試類型、國小
是成績 / 學生 / 作業頁的下拉選單來源，課輔老師沒有 settings:read 也需要讀取）。
寫入（POST）：只認 settings:write，不可沿用讀取的任一權限。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import CurrentStaff, require_any_permission, require_permission
from app.core.db import get_db
from app.core.permissions import Permission
from app.schemas.reference import ReferenceListQuery
from app.services import reference_data_service
from app.services.reference_specs import SPECS, ReferenceSpec

WRITE_PERMISSION = Permission.SETTINGS_WRITE
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

    def create_item(
        _: Annotated[CurrentStaff, Depends(require_permission(WRITE_PERMISSION))],
        body: BaseModel,
        db: Annotated[Session, Depends(get_db)],
    ) -> BaseModel:
        out = reference_data_service.create_item(db, spec, body)
        db.commit()
        return out

    # body 型別依資源而定（執行期才知道 spec.create_schema）
    create_item.__annotations__["body"] = Annotated[spec.create_schema, Body()]
    router.add_api_route(
        "",
        create_item,
        methods=["POST"],
        response_model=spec.out_schema,
        status_code=201,
    )
    return router


reference_routers = [build_reference_router(spec) for spec in SPECS.values()]
