"""後台請假 endpoint（domain_spec M6）。

- BACKEND-351 ``GET /leaves``：leaves:read；``LeaveListQuery`` + 分頁 → ``Page[LeaveOut]``（附件只回
  metadata）。
- BACKEND-352 ``POST /leaves``：leaves:write；``LeaveCreateIn`` → BACKEND-345 ``create_leave``
  （actor = ``Actor.staff``；重疊 409 ``leave_overlap``）→ commit → 201 ``LeaveOut``。LeaveOut 的
  組裝目前只在 ``list_leaves`` 內，這裡以 student_id 篩選列表再取同 id 的那筆。
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
from app.core.clock import Clock, get_clock
from app.core.db import get_db
from app.core.errors import AppError
from app.core.pagination import MAX_PAGE_SIZE, Page, PageParams, page_params
from app.core.permissions import Permission
from app.core.storage import Storage, get_storage
from app.schemas.leaves import AttachmentUrlOut, LeaveCreateIn, LeaveListQuery, LeaveOut
from app.services import leave_service
from app.services.audit_service import Actor

router = APIRouter(prefix="/leaves", tags=["admin-leaves"])

LeavesRead = Annotated[CurrentStaff, Depends(require_permission(Permission.LEAVES_READ))]
LeavesWrite = Annotated[CurrentStaff, Depends(require_permission(Permission.LEAVES_WRITE))]
Db = Annotated[Session, Depends(get_db)]


def _leave_out(db: Session, student_id: UUID, leave_id: UUID) -> LeaveOut:
    page = leave_service.list_leaves(
        db, LeaveListQuery(student_id=student_id), PageParams(page=1, page_size=MAX_PAGE_SIZE)
    )
    for item in page.items:
        if item.id == leave_id:
            return item
    raise AppError("internal_error", "剛建立的請假讀不到", status=500)


@router.get("", response_model=Page[LeaveOut])
def list_leaves(
    _: LeavesRead,
    query: Annotated[LeaveListQuery, Depends(query_model(LeaveListQuery))],
    page: Annotated[PageParams, Depends(page_params)],
    db: Db,
) -> Page[LeaveOut]:
    return leave_service.list_leaves(db, query, page)


@router.post("", status_code=201, response_model=LeaveOut)
def create_leave(
    body: LeaveCreateIn,
    staff: LeavesWrite,
    db: Db,
    clock: Annotated[Clock, Depends(get_clock)],
) -> LeaveOut:
    leave = leave_service.create_leave(db, body, actor=Actor.staff(staff), clock=clock)
    out = _leave_out(db, leave.student_id, leave.id)
    db.commit()
    return out


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
