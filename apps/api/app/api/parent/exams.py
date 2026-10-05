"""BACKEND-479：家長端成績 endpoint。

``GET /api/parent/children/{student_id}/exams?page=&page_size=``：``get_owned_student``（不屬於自己
→ 404）→ BACKEND-465 ``list_child_exams``（只含已發布且該生有成績的考試）。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_owned_student
from app.core.db import get_db
from app.core.pagination import Page, PageParams, page_params
from app.models.students import Student
from app.schemas.exams import ParentExamListItemOut
from app.services import exam_service

router = APIRouter(tags=["parent-exams"])


@router.get("/children/{student_id}/exams", response_model=Page[ParentExamListItemOut])
def list_child_exams(
    student: Annotated[Student, Depends(get_owned_student)],
    page: Annotated[PageParams, Depends(page_params)],
    db: Annotated[Session, Depends(get_db)],
) -> Page[ParentExamListItemOut]:
    return exam_service.list_child_exams(db, student.id, page)
