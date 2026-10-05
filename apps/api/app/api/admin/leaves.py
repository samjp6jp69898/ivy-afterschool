"""後台請假 endpoint（domain_spec M6）。

- BACKEND-351 ``GET /leaves``：leaves:read；``LeaveListQuery`` + 分頁 → ``Page[LeaveOut]``（附件只回
  metadata）。
- BACKEND-354 ``GET /leaves/{leave_id}/attachments/{attachment_id}``：leaves:read；BACKEND-350 簽發
  短效 URL → ``AttachmentUrlOut``，``Cache-Control: no-store``；附件不屬於該請假與不存在皆 404
  ``attachment_not_found``。
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.api.admin._query import query_model
from app.api.deps import CurrentStaff, require_permission
from app.core.db import get_db
from app.core.pagination import Page, PageParams, page_params
from app.core.permissions import Permission
from app.core.storage import Storage, get_storage
from app.schemas.leaves import AttachmentUrlOut, LeaveListQuery, LeaveOut
from app.services import leave_service

router = APIRouter(prefix="/leaves", tags=["admin-leaves"])

LeavesRead = Annotated[CurrentStaff, Depends(require_permission(Permission.LEAVES_READ))]
Db = Annotated[Session, Depends(get_db)]


@router.get("", response_model=Page[LeaveOut])
def list_leaves(
    _: LeavesRead,
    query: Annotated[LeaveListQuery, Depends(query_model(LeaveListQuery))],
    page: Annotated[PageParams, Depends(page_params)],
    db: Db,
) -> Page[LeaveOut]:
    return leave_service.list_leaves(db, query, page)


@router.get("/{leave_id}/attachments/{attachment_id}", response_model=AttachmentUrlOut)
def get_attachment_url(
    leave_id: UUID,
    attachment_id: UUID,
    _: LeavesRead,
    response: Response,
    db: Db,
    storage: Annotated[Storage, Depends(get_storage)],
) -> AttachmentUrlOut:
    out = leave_service.get_attachment_url(db, leave_id, attachment_id, storage=storage)
    response.headers["Cache-Control"] = "no-store"
    return out
