"""BACKEND-159：後台學生 endpoint。

路由順序：固定路徑（``/students/import``、``/students/promote-grade``）必須在
``/students/{student_id}`` 之前註冊，由本模組內的宣告順序保證。

``GET /api/admin/students``：students:read；列表只回 ``StudentListItemOut``（不含任何敏感欄位）。
``GET /api/admin/students/{student_id}/guardians``（BACKEND-173）：students:read；監護人與綁定狀態。
``GET /api/admin/students/{student_id}``（BACKEND-161）：students:read；``sensitive`` 只在持
students:sensitive 時有值（BACKEND-150），封存學生也可查。
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.admin._query import query_model
from app.api.deps import CurrentStaff, require_permission
from app.core.clock import Clock, get_clock
from app.core.db import get_db
from app.core.pagination import Page, PageParams, page_params
from app.core.permissions import Permission
from app.core.storage import Storage, get_storage
from app.schemas.guardians import GuardianOut
from app.schemas.students import StudentDetailOut, StudentListItemOut, StudentListQuery
from app.services import guardian_service, student_service

router = APIRouter(prefix="/students", tags=["admin-students"])


@router.get("", response_model=Page[StudentListItemOut])
def list_students(
    staff: Annotated[CurrentStaff, Depends(require_permission(Permission.STUDENTS_READ))],
    query: Annotated[StudentListQuery, Depends(query_model(StudentListQuery))],
    page: Annotated[PageParams, Depends(page_params)],
    db: Annotated[Session, Depends(get_db)],
) -> Page[StudentListItemOut]:
    return student_service.list_students(db, query, page, actor=staff)


@router.get("/{student_id}/guardians", response_model=list[GuardianOut])
def list_guardians(
    student_id: UUID,
    _: Annotated[CurrentStaff, Depends(require_permission(Permission.STUDENTS_READ))],
    db: Annotated[Session, Depends(get_db)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> list[GuardianOut]:
    return guardian_service.list_for_student(db, student_id, clock=clock)


# 固定路徑（/import、/promote-grade、/import-template）必須宣告在本 handler 之前
@router.get("/{student_id}", response_model=StudentDetailOut)
def get_student(
    student_id: UUID,
    staff: Annotated[CurrentStaff, Depends(require_permission(Permission.STUDENTS_READ))],
    db: Annotated[Session, Depends(get_db)],
    clock: Annotated[Clock, Depends(get_clock)],
    storage: Annotated[Storage, Depends(get_storage)],
) -> StudentDetailOut:
    return student_service.get_student(db, student_id, actor=staff, storage=storage, clock=clock)
