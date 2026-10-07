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
``PATCH /api/admin/students/{student_id}``（BACKEND-162）：students:write → BACKEND-152
``update_student``（改狀態時同交易收尾）→ 回應以 BACKEND-150 ``get_student`` 重組（同一個組裝函式簽
photo_url；update_student 沒有 storage 參數）→ commit → 200 ``StudentDetailOut``。
``GET /api/admin/students/import-template``（BACKEND-533）：students:write → BACKEND-532
``build_import_template``（常數內容、不含使用者輸入）→ 200 xlsx，固定檔名「學生匯入範本.xlsx」、
``Cache-Control: no-store``。
``POST /api/admin/students/promote-grade``（BACKEND-165）：students:write；``PromoteGradeIn``
dry_run=true → BACKEND-157 ``preview``（不寫入）→ ``PromotionPreviewOut``；dry_run=false →
BACKEND-158 ``execute`` → commit → ``PromotionResultOut``。409 already_promoted / preview_stale、
422 invalid_dates 由 service 拋出（不 commit）。
``POST /api/admin/students/import``（BACKEND-166）：students:write；multipart ``file`` /
``academic_year`` / ``dry_run``。上傳檔先經 BACKEND-016 ``read_validated_upload``（分塊讀、5 MiB
上限 413、空檔 422、非 xlsx 415）才交給 service（zip / XML 深度防護在 BACKEND-155 內）：
dry_run=true → ``preview`` → ``ImportPreviewOut``（每列只回 row_number / display / errors，不回
正規化資料）；dry_run=false → BACKEND-156 ``execute`` → commit → ``ImportResultOut``。
409 import_has_errors / import_conflict 由 service 拋出（不 commit）。
"""

from __future__ import annotations

from typing import Annotated, Final
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile
from sqlalchemy.orm import Session

from app.api.admin._query import query_model
from app.api.deps import CurrentStaff, require_permission
from app.core.clock import Clock, get_clock
from app.core.db import get_db
from app.core.pagination import Page, PageParams, page_params
from app.core.permissions import Permission
from app.core.request_meta import RequestMeta, get_request_meta
from app.core.storage import Storage, get_storage
from app.core.uploads import XLSX_MAX_BYTES, XLSX_TYPES, read_validated_upload
from app.schemas.guardians import GuardianCreateIn, GuardianOut
from app.schemas.students import (
    ImportPreviewOut,
    ImportResultOut,
    PhotoUploadOut,
    PromoteGradeIn,
    PromotionPreviewOut,
    PromotionResultOut,
    StudentCreateIn,
    StudentDetailOut,
    StudentListItemOut,
    StudentListQuery,
    StudentPurgeIn,
    StudentPurgeOut,
    StudentUpdateIn,
)
from app.services import (
    grade_promotion_service,
    guardian_service,
    student_import_service,
    student_service,
)
from app.services.student_import_template import build_import_template

router = APIRouter(prefix="/students", tags=["admin-students"])

XLSX_MEDIA_TYPE: Final = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_TEMPLATE_FILENAME: Final = quote("學生匯入範本.xlsx")


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


# --- 固定路徑：一律宣告在 /{student_id} 相關 handler 之前 ----------------------------------------


@router.get("/import-template", response_class=Response)
def download_import_template(
    _: Annotated[CurrentStaff, Depends(require_permission(Permission.STUDENTS_WRITE))],
) -> Response:
    return Response(
        content=build_import_template(),
        media_type=XLSX_MEDIA_TYPE,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{_TEMPLATE_FILENAME}",
            # SecurityMiddleware 已對 /api/ 全域加 no-store；這裡照 description 與出勤匯出明寫
            "Cache-Control": "no-store",
        },
    )


@router.post("/promote-grade", response_model=PromotionPreviewOut | PromotionResultOut)
def promote_grade(
    body: PromoteGradeIn,
    staff: Annotated[CurrentStaff, Depends(require_permission(Permission.STUDENTS_WRITE))],
    db: Annotated[Session, Depends(get_db)],
    meta: Annotated[RequestMeta, Depends(get_request_meta)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> PromotionPreviewOut | PromotionResultOut:
    if body.dry_run:
        preview = grade_promotion_service.preview(db, from_academic_year=body.from_academic_year)
        return PromotionPreviewOut.model_validate(preview)
    assert body.expected_total is not None  # noqa: S101  PromoteGradeIn 已保證執行時必填
    result = grade_promotion_service.execute(
        db,
        from_academic_year=body.from_academic_year,
        expected_total=body.expected_total,
        withdrawn_on=body.withdrawn_on,
        actor=staff,
        meta=meta,
        clock=clock,
    )
    db.commit()
    return PromotionResultOut(promoted=result.promoted, graduated=result.graduated)


@router.post("/import", response_model=ImportPreviewOut | ImportResultOut)
def import_students(
    file: Annotated[UploadFile, File()],
    academic_year: Annotated[int, Form(ge=100, le=200)],
    staff: Annotated[CurrentStaff, Depends(require_permission(Permission.STUDENTS_WRITE))],
    db: Annotated[Session, Depends(get_db)],
    meta: Annotated[RequestMeta, Depends(get_request_meta)],
    clock: Annotated[Clock, Depends(get_clock)],
    dry_run: Annotated[bool, Form()] = True,
) -> ImportPreviewOut | ImportResultOut:
    # 不受信任的上傳檔：分塊讀、超過上限即 413、檔頭非 xlsx 415；之後的 zip / XML 防護在 service 內
    upload = read_validated_upload(file, allowed=XLSX_TYPES, max_bytes=XLSX_MAX_BYTES)
    if dry_run:
        preview = student_import_service.preview(
            db, upload, academic_year=academic_year, actor=staff
        )
        return ImportPreviewOut.model_validate(preview)
    result = student_import_service.execute(
        db, upload, academic_year=academic_year, actor=staff, meta=meta, clock=clock
    )
    db.commit()
    return ImportResultOut(created=result.created, student_ids=result.student_ids)


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


@router.patch("/{student_id}", response_model=StudentDetailOut)
def update_student(
    student_id: UUID,
    body: StudentUpdateIn,
    staff: Annotated[CurrentStaff, Depends(require_permission(Permission.STUDENTS_WRITE))],
    db: Annotated[Session, Depends(get_db)],
    meta: Annotated[RequestMeta, Depends(get_request_meta)],
    clock: Annotated[Clock, Depends(get_clock)],
    storage: Annotated[Storage, Depends(get_storage)],
) -> StudentDetailOut:
    student_service.update_student(db, student_id, body, actor=staff, meta=meta, clock=clock)
    # update_student 沒有 storage、自己回的 photo_url 恆 None：以 GET 詳情同一個組裝函式重組（簽名）
    out = student_service.get_student(db, student_id, actor=staff, storage=storage, clock=clock)
    db.commit()
    return out
