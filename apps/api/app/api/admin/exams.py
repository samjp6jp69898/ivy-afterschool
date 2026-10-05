"""後台考試成績 endpoint（domain_spec M10）。寫入型 handler 呼叫 service 後自行 ``db.commit()``。

- BACKEND-467 ``GET /exams``：exams:read；``ExamListQuery`` + 分頁 → ``Page[ExamOut]``。
- BACKEND-468 ``POST /exams``：exams:write；``ExamCreateIn`` → 201 ``ExamOut``（draft、不含科目）。
- BACKEND-469 ``GET /exams/{exam_id}``：exams:read → ``ExamOut``。
- BACKEND-471 ``DELETE /exams/{exam_id}``：exams:write；只能刪草稿（published → 409）→ 204。
- BACKEND-472 ``PUT /exams/{exam_id}/subjects``：exams:write；``ExamSubjectsPutIn`` → ``ExamOut``。
- BACKEND-473 ``GET /exams/{exam_id}/scores``：exams:read → ``ScoreGridOut``（學生對科目的格狀資料）。
- BACKEND-476 ``POST /exams/{exam_id}/unpublish``：exams:publish；無 body，寫 audit → ``ExamOut``。
- BACKEND-477 ``GET /exams/{exam_id}/summary``：exams:read → ``ExamSummaryOut``（各科平均）。
- BACKEND-478 ``GET /students/{student_id}/exam-history``：exams:read；掛在本模組的
  ``student_exams_router``（與 students.py 共用 ``/students`` 前綴，路徑不衝突）→
  ``list[StudentExamHistoryItemOut]``。
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
from app.core.pagination import Page, PageParams, page_params
from app.core.permissions import Permission
from app.core.request_meta import RequestMeta, get_request_meta
from app.schemas.exams import (
    ExamCreateIn,
    ExamListQuery,
    ExamOut,
    ExamSubjectsPutIn,
    ExamSummaryOut,
    ScoreGridOut,
    StudentExamHistoryItemOut,
)
from app.services import exam_service

router = APIRouter(prefix="/exams", tags=["admin-exams"])
student_exams_router = APIRouter(prefix="/students", tags=["admin-exams"])

ExamsRead = Annotated[CurrentStaff, Depends(require_permission(Permission.EXAMS_READ))]
ExamsWrite = Annotated[CurrentStaff, Depends(require_permission(Permission.EXAMS_WRITE))]
ExamsPublish = Annotated[CurrentStaff, Depends(require_permission(Permission.EXAMS_PUBLISH))]
Db = Annotated[Session, Depends(get_db)]


@router.get("", response_model=Page[ExamOut])
def list_exams(
    _: ExamsRead,
    query: Annotated[ExamListQuery, Depends(query_model(ExamListQuery))],
    page: Annotated[PageParams, Depends(page_params)],
    db: Db,
) -> Page[ExamOut]:
    return exam_service.list_exams(db, query, page)


@router.post("", status_code=201, response_model=ExamOut)
def create_exam(body: ExamCreateIn, staff: ExamsWrite, db: Db) -> ExamOut:
    out = exam_service.create_exam(db, body, actor=staff)
    db.commit()
    return out


@router.get("/{exam_id}", response_model=ExamOut)
def get_exam(exam_id: UUID, _: ExamsRead, db: Db) -> ExamOut:
    return exam_service.get_exam(db, exam_id)


@router.delete("/{exam_id}", status_code=204, response_class=Response)
def delete_exam(exam_id: UUID, staff: ExamsWrite, db: Db) -> Response:
    exam_service.delete_exam(db, exam_id, actor=staff)
    db.commit()
    return Response(status_code=204)


@router.put("/{exam_id}/subjects", response_model=ExamOut)
def set_exam_subjects(exam_id: UUID, body: ExamSubjectsPutIn, staff: ExamsWrite, db: Db) -> ExamOut:
    out = exam_service.set_exam_subjects(db, exam_id, body, actor=staff)
    db.commit()
    return out


@router.get("/{exam_id}/scores", response_model=ScoreGridOut)
def get_score_grid(exam_id: UUID, _: ExamsRead, db: Db) -> ScoreGridOut:
    return exam_service.get_score_grid(db, exam_id)


@router.post("/{exam_id}/unpublish", response_model=ExamOut)
def unpublish_exam(
    exam_id: UUID,
    staff: ExamsPublish,
    db: Db,
    meta: Annotated[RequestMeta, Depends(get_request_meta)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> ExamOut:
    out = exam_service.unpublish_exam(db, exam_id, actor=staff, meta=meta, clock=clock)
    db.commit()
    return out


@router.get("/{exam_id}/summary", response_model=ExamSummaryOut)
def get_exam_summary(exam_id: UUID, _: ExamsRead, db: Db) -> ExamSummaryOut:
    return exam_service.get_exam_summary(db, exam_id)


@student_exams_router.get(
    "/{student_id}/exam-history", response_model=list[StudentExamHistoryItemOut]
)
def get_student_exam_history(
    student_id: UUID, _: ExamsRead, db: Db
) -> list[StudentExamHistoryItemOut]:
    return exam_service.get_student_exam_history(db, student_id)
