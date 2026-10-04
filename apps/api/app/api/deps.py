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

BACKEND-062：家長守衛 ``get_current_parent``（architecture_decisions §6 ``require_parent()``；移植
ivy ``api/parent_portal/_shared.py::_get_parent_user``）。

- 只讀 ``parent_access`` cookie；員工 token（typ=staff）、家長不存在、``status='disabled'``、
  ``token_version`` 不符一律 401 ``unauthenticated``。
- **不**檢查是否有綁定小孩（無小孩的家長仍需能呼叫 /me 與 /bind）；學生層級授權一律由
  BACKEND-180 ``assert_parent_owns_student`` 處理。
- ``get_optional_parent``：驗證失敗回 None 不拋例外（給 /bind 判斷是首次綁定或加綁）。
- ``load_current_parent`` 同樣供 WebSocket 重用；``get_current_parent`` 把結果存進
  ``request.state.current_parent``。
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
from app.core.security.cookies import PARENT_ACCESS, STAFF_ACCESS, read_cookie
from app.core.security.tokens import decode_access_token
from app.models.account import StaffUser
from app.models.parents import ParentAccount

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


@dataclass(frozen=True)
class CurrentParent:
    id: UUID
    line_user_id: str
    display_name: str | None
    token_version: int


def load_current_parent(db: Session, token: str | None, *, clock: Clock) -> CurrentParent:
    """驗證家長 access token 並載入帳號；任何失敗一律 401。"""
    if not token:
        raise UnauthenticatedError
    claims = decode_access_token(token, expected_type="parent", clock=clock)
    parent = db.execute(
        select(ParentAccount).where(ParentAccount.id == claims.subject_id)
    ).scalar_one_or_none()
    if (
        parent is None
        or parent.status == "disabled"
        or parent.token_version != claims.token_version
    ):
        raise UnauthenticatedError
    return CurrentParent(
        id=parent.id,
        line_user_id=parent.line_user_id,
        display_name=parent.display_name,
        token_version=parent.token_version,
    )


def get_current_parent(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> CurrentParent:
    """FastAPI dependency：家長登入守衛。"""
    parent = load_current_parent(db, read_cookie(request, PARENT_ACCESS.name), clock=clock)
    request.state.current_parent = parent
    return parent


def get_optional_parent(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> CurrentParent | None:
    """同 get_current_parent，但驗證失敗回 None（只吞 401，其他例外照常拋出）。"""
    try:
        return get_current_parent(request, db, clock)
    except UnauthenticatedError:
        return None
