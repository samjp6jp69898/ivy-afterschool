"""BACKEND-047：員工守衛 ``get_current_staff``（移植 ivy ``utils/auth.py::get_current_user`` 與
``_resolve_user_auth_fields``；去掉 Bearer header fallback、tenant、jti blocklist、impersonation、
guest token）。

- 只讀 ``staff_access`` cookie（BACKEND-035）；無 cookie、解碼失敗（含家長 token）、帳號不存在、
  停用、``token_version`` 不符一律 401 ``unauthenticated``（改密碼 / 停用 / 重設後舊 token 立即
  失效）。
- ``must_change_password`` 時只放行 ``PASSWORD_CHANGE_ALLOWED_PATHS``，其餘 403
  ``password_change_required``；``path=None``（WebSocket）視為不在 allowlist。
- 有效權限每個請求即時以 BACKEND-072 計算，角色權限變更立即生效。
- 核心邏輯 ``load_current_staff`` 不依賴 Request，供 WebSocket（BACKEND-225）重用；
  ``get_current_staff`` 另把結果存進 ``request.state.current_staff`` 供 log / audit。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Final
from uuid import UUID

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import Clock, get_clock
from app.core.db import get_db
from app.core.errors import ForbiddenError, UnauthenticatedError
from app.core.permissions import Permission, resolve_effective_permissions
from app.core.security.cookies import STAFF_ACCESS, read_cookie
from app.core.security.tokens import decode_access_token
from app.models.account import StaffUser

PASSWORD_CHANGE_ALLOWED_PATHS: Final = frozenset(
    {"/api/admin/auth/me", "/api/admin/auth/change-password", "/api/admin/auth/logout"}
)


@dataclass(frozen=True)
class CurrentStaff:
    id: UUID
    username: str
    display_name: str
    role_id: UUID
    role_code: str
    role_name: str
    permissions: frozenset[str]
    must_change_password: bool
    token_version: int

    def has(self, permission: Permission | str) -> bool:
        return str(permission) in self.permissions


def load_current_staff(
    db: Session, token: str | None, *, clock: Clock, path: str | None
) -> CurrentStaff:
    """驗證 access token 並載入員工；失敗一律 401，須改密碼且 path 不在 allowlist 時 403。"""
    if not token:
        raise UnauthenticatedError
    claims = decode_access_token(token, expected_type="staff", clock=clock)
    # StaffUser.role 為 lazy='joined'，一次查詢同時取得角色
    staff = db.execute(
        select(StaffUser).where(StaffUser.id == claims.subject_id)
    ).scalar_one_or_none()
    if staff is None or not staff.is_active or staff.token_version != claims.token_version:
        raise UnauthenticatedError
    if staff.must_change_password and (path is None or path not in PASSWORD_CHANGE_ALLOWED_PATHS):
        raise ForbiddenError("需先修改密碼後才能使用系統", code="password_change_required")
    role = staff.role
    return CurrentStaff(
        id=staff.id,
        username=staff.username,
        display_name=staff.display_name,
        role_id=role.id,
        role_code=role.code,
        role_name=role.name,
        permissions=resolve_effective_permissions(
            role.permissions, staff.extra_permissions, staff.revoked_permissions
        ),
        must_change_password=staff.must_change_password,
        token_version=staff.token_version,
    )


def get_current_staff(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> CurrentStaff:
    """FastAPI dependency：員工登入守衛。"""
    staff = load_current_staff(
        db, read_cookie(request, STAFF_ACCESS.name), clock=clock, path=request.url.path
    )
    request.state.current_staff = staff
    return staff
