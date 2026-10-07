"""家長端請假 endpoint。

- BACKEND-355 ``GET /api/parent/children/{student_id}/leaves``：``get_owned_student``（不屬於自己 /
  不存在 / 封存皆同一 404）→ BACKEND-348 ``list_child_leaves``（含已取消、附件短效 URL、
  can_cancel）。
- BACKEND-356 ``POST /api/parent/leaves``：BACKEND-345 → 201 ``ParentLeaveOut``。
- BACKEND-357 ``POST /api/parent/leaves/{leave_id}/cancel``：無 body、scope 固定 remaining →
  BACKEND-346（他人的請假與不存在同一 404；未開始整筆取消、已開始 end_date 縮為昨天）→ 200
  ``ParentLeaveOut``（含附件短效 URL）。
- BACKEND-358 ``POST /api/parent/leaves/{leave_id}/attachments``：multipart 欄位 ``file`` →
  BACKEND-349（他人的請假與不存在同一 404）→ 201 ``ParentLeaveAttachmentOut``。

寫入型 handler 呼叫 service 後自行 ``db.commit()``。
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.orm import Session

from app.api.deps import CurrentParent, get_current_parent, get_owned_student
from app.core.clock import Clock, get_clock
from app.core.db import get_db
from app.core.pagination import Page, PageParams, page_params
from app.core.storage import Storage, StorageError, get_storage
from app.models.leaves import LEAVE_TYPE_LABELS, StudentLeave, StudentLeaveAttachment
from app.models.students import Student
from app.schemas.leaves import ParentLeaveAttachmentOut, ParentLeaveCreateIn, ParentLeaveOut
from app.services import leave_service
from app.services.audit_service import Actor

logger = logging.getLogger(__name__)

router = APIRouter(tags=["parent-leaves"])


@router.get("/children/{student_id}/leaves", response_model=Page[ParentLeaveOut])
def list_child_leaves(
    student: Annotated[Student, Depends(get_owned_student)],
    page: Annotated[PageParams, Depends(page_params)],
    db: Annotated[Session, Depends(get_db)],
    storage: Annotated[Storage, Depends(get_storage)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> Page[ParentLeaveOut]:
    return leave_service.list_child_leaves(db, student.id, page, storage=storage, clock=clock)


def _signed_url_or_none(storage: Storage, attachment: StudentLeaveAttachment) -> str | None:
    """簽名失敗不讓整個回應失敗（取消已生效）：回 None 並記 warning，與列表（BACKEND-348）一致。"""
    try:
        return storage.create_signed_url(
            "leave-attachments", attachment.storage_path, leave_service.ATTACHMENT_URL_SECONDS
        )
    except StorageError:
        logger.warning("請假附件簽名失敗 attachment_id=%s", attachment.id)
        return None


def _attachment_outs(leave: StudentLeave, storage: Storage) -> list[ParentLeaveAttachmentOut]:
    return [
        ParentLeaveAttachmentOut(
            id=a.id,
            mime_type=a.mime_type,
            size_bytes=a.size_bytes,
            created_at=a.created_at,
            url=_signed_url_or_none(storage, a),
        )
        for a in leave.attachments
    ]


def _parent_leave_out(
    leave: StudentLeave, *, today: date, attachments: list[ParentLeaveAttachmentOut]
) -> ParentLeaveOut:
    """單筆請假；欄位對齊 BACKEND-348 的列表輸出。剛建立的請假沒有附件，傳空清單。"""
    return ParentLeaveOut(
        id=leave.id,
        student_id=leave.student_id,
        leave_type=leave.leave_type,
        leave_type_label=LEAVE_TYPE_LABELS[leave.leave_type],
        start_date=leave.start_date,
        end_date=leave.end_date,
        reason=leave.reason,
        status=leave.status,
        created_by_type=leave.created_by_type,
        created_at=leave.created_at,
        cancelled_at=leave.cancelled_at,
        can_cancel=leave.status == "active" and leave.end_date >= today,
        attachments=attachments,
    )


@router.post("/leaves", response_model=ParentLeaveOut, status_code=201)
def create_leave(
    body: ParentLeaveCreateIn,
    parent: Annotated[CurrentParent, Depends(get_current_parent)],
    db: Annotated[Session, Depends(get_db)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> ParentLeaveOut:
    leave = leave_service.create_leave(db, body, actor=Actor.parent(parent), clock=clock)
    out = _parent_leave_out(leave, today=clock.today(), attachments=[])
    db.commit()
    return out


@router.post("/leaves/{leave_id}/cancel", response_model=ParentLeaveOut)
def cancel_leave(
    leave_id: UUID,
    parent: Annotated[CurrentParent, Depends(get_current_parent)],
    db: Annotated[Session, Depends(get_db)],
    storage: Annotated[Storage, Depends(get_storage)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> ParentLeaveOut:
    result = leave_service.cancel_leave(db, leave_id, actor=Actor.parent(parent), clock=clock)
    out = _parent_leave_out(
        result.leave, today=clock.today(), attachments=_attachment_outs(result.leave, storage)
    )
    db.commit()
    return out


@router.post(
    "/leaves/{leave_id}/attachments", response_model=ParentLeaveAttachmentOut, status_code=201
)
def upload_leave_attachment(
    leave_id: UUID,
    file: Annotated[UploadFile, File()],
    parent: Annotated[CurrentParent, Depends(get_current_parent)],
    db: Annotated[Session, Depends(get_db)],
    storage: Annotated[Storage, Depends(get_storage)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> ParentLeaveAttachmentOut:
    out = leave_service.upload_leave_attachment(
        db, leave_id, file, parent=parent, storage=storage, clock=clock
    )
    db.commit()
    return out
