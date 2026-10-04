"""BACKEND-076：角色模組 schemas（欄位限制對齊 DB-003 CHECK）。"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field

from app.schemas.common import OutModel, RequestModel, UpdateModel

ROLE_CODE_PATTERN = r"^[a-z][a-z0-9_]{1,31}$"


class RoleCreateIn(RequestModel):
    code: str = Field(pattern=ROLE_CODE_PATTERN)
    name: str = Field(min_length=1, max_length=50)
    description: str | None = Field(default=None, max_length=200)
    # 合法性由 service 以 permission 目錄驗證（回傳一致的錯誤碼）
    permissions: list[str] = Field(max_length=50)


class RoleUpdateIn(UpdateModel):
    nullable_fields = frozenset({"description"})

    name: str | None = Field(default=None, min_length=1, max_length=50)
    description: str | None = Field(default=None, max_length=200)
    permissions: list[str] | None = Field(default=None, max_length=50)


class RoleOut(OutModel):
    id: UUID
    code: str
    name: str
    description: str | None
    is_system: bool
    permissions: list[str]  # DB 原值，admin 為 ['*']
    effective_permissions: list[str]  # 展開後排序
    staff_count: int
    created_at: datetime
    updated_at: datetime


class PermissionItemOut(OutModel):
    code: str
    label: str


class PermissionGroupOut(OutModel):
    key: str
    label: str
    permissions: list[PermissionItemOut]


class PermissionCatalogOut(OutModel):
    groups: list[PermissionGroupOut]
