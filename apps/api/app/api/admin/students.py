"""BACKEND-159：後台學生 endpoint。

路由順序：固定路徑（``/students/import``、``/students/promote-grade``）必須在
``/students/{student_id}`` 之前註冊，由本模組內的宣告順序保證。

``GET /api/admin/students``：students:read；列表只回 ``StudentListItemOut``（不含任何敏感欄位）。
``GET /api/admin/students/{student_id}/guardians``（BACKEND-173）：students:read；監護人與綁定狀態。
``GET /api/admin/students/{student_id}``（BACKEND-161）：students:read；``sensitive`` 只在持
students:sensitive 時有值（BACKEND-150），封存學生也可查。
``POST /api/admin/students``（BACKEND-160）：students:write → BACKEND-151 → 201（敏感欄位另需
students:sensitive，由 service 檢查）。
``POST /api/admin/students/{student_id}/archive``（BACKEND-163）：students:write → BACKEND-153
→ 200。
``POST /api/admin/students/{student_id}/guardians``（BACKEND-174）：guardians:write →
BACKEND-169 → 201。
``POST /api/admin/students/{student_id}/purge``（BACKEND-531）：students:purge（預設只有 admin）→
BACKEND-530 → 200。
``POST /api/admin/students/{student_id}/photo``（BACKEND-164）：multipart 欄位 ``file``、
students:write → BACKEND-154 ``upload_photo`` → 200 ``PhotoUploadOut``（短效 URL）。
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.orm import Session

from app.api.admin._query import query_model
from app.api.deps import CurrentStaff, require_permission
from app.core.clock import Clock, get_clock
from app.core.db import get_db
from app.core.pagination import Page, PageParams, page_params
from app.core.permissions import Permission
from app.core.request_meta import RequestMeta, get_request_meta
from app.core.storage import Storage, get_storage
from app.schemas.guardians import GuardianCreateIn, GuardianOut
from app.schemas.students import (
    PhotoUploadOut,
    StudentCreateIn,
    StudentDetailOut,
    StudentListItemOut,
    StudentListQuery,
    StudentPurgeIn,
    StudentPurgeOut,
)
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


@router.post("", response_model=StudentDetailOut, status_code=201)
def create_student(
    body: StudentCreateIn,
    staff: Annotated[CurrentStaff, Depends(require_permission(Permission.STUDENTS_WRITE))],
    db: Annotated[Session, Depends(get_db)],
    meta: Annotated[RequestMeta, Depends(get_request_meta)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> StudentDetailOut:
    out = student_service.create_student(db, body, actor=staff, meta=meta, clock=clock)
    db.commit()
    return out


@router.post("/{student_id}/archive", response_model=StudentDetailOut)
def archive_student(
    student_id: UUID,
    staff: Annotated[CurrentStaff, Depends(require_permission(Permission.STUDENTS_WRITE))],
    db: Annotated[Session, Depends(get_db)],
    clock: Annotated[Clock, Depends(get_clock)],
    storage: Annotated[Storage, Depends(get_storage)],
) -> StudentDetailOut:
    out = student_service.archive_student(db, student_id, clock=clock, actor=staff, storage=storage)
    db.commit()
    return out


@router.post("/{student_id}/purge", response_model=StudentPurgeOut)
def purge_student(
    student_id: UUID,
    body: StudentPurgeIn,
    staff: Annotated[CurrentStaff, Depends(require_permission(Permission.STUDENTS_PURGE))],
    db: Annotated[Session, Depends(get_db)],
    storage: Annotated[Storage, Depends(get_storage)],
    meta: Annotated[RequestMeta, Depends(get_request_meta)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> StudentPurgeOut:
    out = student_service.purge_student(
        db, student_id, body, actor=staff, storage=storage, meta=meta, clock=clock
    )
    db.commit()
    return out


@router.post("/{student_id}/photo", response_model=PhotoUploadOut)
def upload_photo(
    student_id: UUID,
    file: Annotated[UploadFile, File()],
    _: Annotated[CurrentStaff, Depends(require_permission(Permission.STUDENTS_WRITE))],
    db: Annotated[Session, Depends(get_db)],
    storage: Annotated[Storage, Depends(get_storage)],
) -> PhotoUploadOut:
    out = student_service.upload_photo(db, student_id, file, storage=storage)
    db.commit()
    return out


@router.post("/{student_id}/guardians", response_model=GuardianOut, status_code=201)
def create_guardian(
    student_id: UUID,
    body: GuardianCreateIn,
    _: Annotated[CurrentStaff, Depends(require_permission(Permission.GUARDIANS_WRITE))],
    db: Annotated[Session, Depends(get_db)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> GuardianOut:
    out = guardian_service.create_guardian(db, student_id, body, clock=clock)
    db.commit()
    return out


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
