"""BACKEND-177：後台監護人 endpoint（``/api/admin/guardians/*``；``/students/{id}/guardians`` 在
students.py）。

``POST /guardians/{guardian_id}/binding-code``：guardians:write；無 body → BACKEND-054
``binding_code_service.generate`` → commit → 201 ``BindingCodeOut``。明碼只在此回傳一次，回應加
``Cache-Control: no-store``（瀏覽器與中介不得快取）。404 / 409 由 service 拋出。
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.api.deps import CurrentStaff, require_permission
from app.core.clock import Clock, get_clock
from app.core.db import get_db
from app.core.permissions import Permission
from app.core.request_meta import RequestMeta, get_request_meta
from app.schemas.guardians import BindingCodeOut
from app.services import binding_code_service

router = APIRouter(prefix="/guardians", tags=["admin-guardians"])


@router.post("/{guardian_id}/binding-code", status_code=201, response_model=BindingCodeOut)
def create_binding_code(
    guardian_id: UUID,
    response: Response,
    staff: Annotated[CurrentStaff, Depends(require_permission(Permission.GUARDIANS_WRITE))],
    db: Annotated[Session, Depends(get_db)],
    meta: Annotated[RequestMeta, Depends(get_request_meta)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> BindingCodeOut:
    issued = binding_code_service.generate(
        db, guardian_id=guardian_id, actor=staff, meta=meta, clock=clock
    )
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return BindingCodeOut(
        guardian_id=issued.guardian_id, code=issued.code, expires_at=issued.expires_at
    )
