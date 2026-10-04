"""BACKEND-159：後台學生 endpoint。

路由順序：固定路徑（``/students/import``、``/students/promote-grade``）必須在
``/students/{student_id}`` 之前註冊，由本模組內的宣告順序保證。

``GET /api/admin/students``：students:read；列表只回 ``StudentListItemOut``（不含任何敏感欄位）。
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
from app.schemas.students import StudentListItemOut, StudentListQuery
from app.services import student_service

router = APIRouter(prefix="/students", tags=["admin-students"])


@router.get("", response_model=Page[StudentListItemOut])
def list_students(
    staff: Annotated[CurrentStaff, Depends(require_permission(Permission.STUDENTS_READ))],
    query: Annotated[StudentListQuery, Depends(query_model(StudentListQuery))],
    page: Annotated[PageParams, Depends(page_params)],
    db: Annotated[Session, Depends(get_db)],
) -> Page[StudentListItemOut]:
    return student_service.list_students(db, query, page, actor=staff)
