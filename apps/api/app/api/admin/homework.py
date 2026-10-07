"""後台作業進度 endpoint（domain_spec M7）。寫入型 handler 呼叫 service 後自行 ``db.commit()``。

- BACKEND-385 ``GET /homework/board``：homework:read；``BoardQuery``（date 預設今天、class_id）→
  BACKEND-383 ``get_board`` → ``BoardOut``。
- BACKEND-386 ``POST /homework/items``：homework:write；``HomeworkItemCreateIn`` → BACKEND-377
  ``create_item`` → commit → 201 ``HomeworkMutationOut``（項目與重算後的進度）。
- BACKEND-387 ``POST /homework/items/batch``：homework:write；``HomeworkBatchCreateIn`` →
  BACKEND-378 ``batch_create_items``（整班或班內指定學生）→ commit → 201 ``HomeworkBatchOut``。

固定路徑 ``/items/batch`` 宣告在 ``/items/{item_id}`` 之前。
- BACKEND-390 ``PUT /homework/progress/{student_id}``：homework:write；``ProgressPutIn``
  （service_date 預設今天）→ 有給 overall 先 BACKEND-381 ``set_overall_status``，有給
  ready_eta / note 再 BACKEND-382 ``set_ready_eta_and_note``（未給的欄位傳 UNSET）→ commit →
  ``ProgressOut``。只給 overall 時以 381 回傳的進度列組回應（規則同 382）。
"""

from __future__ import annotations

from datetime import time
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.admin._query import query_model
from app.api.deps import CurrentStaff, require_permission
from app.core.clock import Clock, get_clock
from app.core.db import get_db
from app.core.permissions import Permission
from app.models.account import StaffUser
from app.models.homework import HomeworkDailyProgress
from app.schemas.homework import (
    BoardOut,
    BoardQuery,
    HomeworkBatchCreateIn,
    HomeworkBatchOut,
    HomeworkItemCreateIn,
    HomeworkMutationOut,
    ProgressOut,
    ProgressPutIn,
)
from app.services import homework_service
from app.services.homework_service import UNSET, ProgressChange

router = APIRouter(prefix="/homework", tags=["admin-homework"])

HomeworkWrite = Annotated[CurrentStaff, Depends(require_permission(Permission.HOMEWORK_WRITE))]
Db = Annotated[Session, Depends(get_db)]
ClockDep = Annotated[Clock, Depends(get_clock)]


@router.get("/board", response_model=BoardOut)
def get_board(
    _: Annotated[CurrentStaff, Depends(require_permission(Permission.HOMEWORK_READ))],
    query: Annotated[BoardQuery, Depends(query_model(BoardQuery))],
    db: Db,
    clock: ClockDep,
) -> BoardOut:
    return homework_service.get_board(db, query, clock=clock)


@router.post("/items", status_code=201, response_model=HomeworkMutationOut)
def create_item(
    body: HomeworkItemCreateIn, staff: HomeworkWrite, db: Db, clock: ClockDep
) -> HomeworkMutationOut:
    out = homework_service.create_item(db, body, actor=staff, clock=clock)
    db.commit()
    return out


@router.post("/items/batch", status_code=201, response_model=HomeworkBatchOut)
def batch_create_items(
    body: HomeworkBatchCreateIn, staff: HomeworkWrite, db: Db, clock: ClockDep
) -> HomeworkBatchOut:
    out = homework_service.batch_create_items(db, body, actor=staff, clock=clock)
    db.commit()
    return out


def _progress_out(db: Session, progress: HomeworkDailyProgress) -> ProgressOut:
    """進度列 → ``ProgressOut``（規則同 BACKEND-382：ready_eta 為 ``HH:MM``、
    eta_updated_by_name 為最後修改 ETA 的員工）。"""
    editor = db.get(StaffUser, progress.eta_updated_by) if progress.eta_updated_by else None
    return ProgressOut(
        student_id=progress.student_id,
        service_date=progress.service_date,
        overall_status=progress.overall_status,
        ready_eta=None if progress.ready_eta is None else progress.ready_eta.strftime("%H:%M"),
        note=progress.note,
        eta_updated_at=progress.eta_updated_at,
        eta_updated_by_name=None if editor is None else editor.display_name,
    )


@router.put("/progress/{student_id}", response_model=ProgressOut)
def put_progress(
    student_id: UUID, body: ProgressPutIn, staff: HomeworkWrite, db: Db, clock: ClockDep
) -> ProgressOut:
    d = body.service_date or clock.today()
    given = body.model_fields_set
    change: ProgressChange | None = None
    if body.overall is not None:
        change = homework_service.set_overall_status(
            db, student_id, d, body.overall, actor=staff, clock=clock
        )
    if change is not None and not given & {"ready_eta", "note"}:
        out = _progress_out(db, change.progress)
    else:
        # ProgressPutIn 保證除 service_date 外至少給一個欄位：沒給 overall 時必有 ready_eta / note
        out = homework_service.set_ready_eta_and_note(
            db,
            student_id,
            d,
            ready_eta=(
                (None if body.ready_eta is None else time.fromisoformat(body.ready_eta))
                if "ready_eta" in given
                else UNSET
            ),
            note=body.note if "note" in given else UNSET,
            actor=staff,
            clock=clock,
        )
    db.commit()
    return out
