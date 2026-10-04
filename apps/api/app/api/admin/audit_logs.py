"""BACKEND-105：後台稽核紀錄 endpoint。

``GET /api/admin/audit-logs``：audit:read。篩選參數由 ``AuditLogQuery`` 驗證（拒絕未知參數，經
``query_model`` 排除 page / page_size），分頁由 ``page_params`` 驗證。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.admin._query import query_model
from app.api.deps import CurrentStaff, require_permission
from app.core.db import get_db
from app.core.pagination import Page, PageParams, page_params
from app.core.permissions import Permission
from app.schemas.audit import AuditLogOut, AuditLogQuery
from app.services import audit_service

router = APIRouter(prefix="/audit-logs", tags=["admin-audit-logs"])


@router.get("", response_model=Page[AuditLogOut])
def list_audit_logs(
    _: Annotated[CurrentStaff, Depends(require_permission(Permission.AUDIT_READ))],
    query: Annotated[AuditLogQuery, Depends(query_model(AuditLogQuery))],
    page: Annotated[PageParams, Depends(page_params)],
    db: Annotated[Session, Depends(get_db)],
) -> Page[AuditLogOut]:
    return audit_service.list_audit_logs(db, query, page)
