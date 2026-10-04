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

BACKEND-073：權限守衛 factory ``require_permission``（all-of）/ ``require_any_permission``
（any-of），移植 ivy ``utils/auth.py::require_permission`` / ``require_any_permission``。所有
``/api/admin/*`` 路由（auth 與個人收件匣除外）都必須掛它，``tests/support/route_audit.py``
掃描未掛守衛的路由。

BACKEND-180：家長端 path 參數 ``student_id`` 的 IDOR 守衛 ``get_owned_student`` /
``get_owned_student_for_write``（包 ``services/parent_scope.py::assert_parent_owns_student``，
不屬於自己的小孩一律 404）。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Final, Literal
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
from app.models.students import Student
from app.services.parent_scope import assert_parent_owns_student

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


# require_permission / require_any_permission 產生的 dependency 都掛這個屬性，供
# tests/support/route_audit.py 掃描未掛守衛的 /api/admin 路由
PERMISSION_GUARD_ATTR: Final = "__afterschool_permission_guard__"
PERMISSION_DENIED_MESSAGE: Final = "您沒有此功能的權限"


def _check_permission_args(permissions: tuple[Permission, ...]) -> list[str]:
    """factory 參數必須是至少一個 Permission（字串會在啟動時 TypeError，避免拼錯）。"""
    if not permissions:
        raise TypeError("至少要指定一個 Permission")
    for permission in permissions:
        if not isinstance(permission, Permission):
            raise TypeError(f"權限守衛只接受 Permission enum，收到 {permission!r}")
    return sorted(str(p) for p in permissions)


def _permission_guard(
    permissions: tuple[Permission, ...], *, mode: Literal["all", "any"]
) -> Callable[..., CurrentStaff]:
    required = _check_permission_args(permissions)
    check = all if mode == "all" else any

    def dependency(staff: Annotated[CurrentStaff, Depends(get_current_staff)]) -> CurrentStaff:
        if not check(staff.has(p) for p in permissions):
            raise ForbiddenError(
                PERMISSION_DENIED_MESSAGE,
                code="permission_denied",
                details={"required": required},
            )
        return staff

    setattr(dependency, PERMISSION_GUARD_ATTR, True)
    return dependency


def require_permission(*permissions: Permission) -> Callable[..., CurrentStaff]:
    """後台權限守衛（all-of）：``staff = Depends(require_permission(Permission.STUDENTS_WRITE))``。

    缺任一 → 403 ``permission_denied``，``details.required`` 列出全部需要的碼；未登入由
    ``get_current_staff`` 先拋 401。
    """
    return _permission_guard(permissions, mode="all")


def require_any_permission(*permissions: Permission) -> Callable[..., CurrentStaff]:
    """後台權限守衛（any-of）：持有其中任一即放行。"""
    return _permission_guard(permissions, mode="any")


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


def get_owned_student(
    student_id: UUID,
    parent: Annotated[CurrentParent, Depends(get_current_parent)],
    db: Annotated[Session, Depends(get_db)],
) -> Student:
    """家長端讀取用：path 的 student_id 必須是自己的小孩，否則 404（與不存在相同）。"""
    return assert_parent_owns_student(db, parent.id, student_id)


def get_owned_student_for_write(
    student_id: UUID,
    parent: Annotated[CurrentParent, Depends(get_current_parent)],
    db: Annotated[Session, Depends(get_db)],
) -> Student:
    """家長端寫入用：同 get_owned_student，另對 withdrawn 學生 409 ``student_not_active``。"""
    return assert_parent_owns_student(db, parent.id, student_id, for_write=True)
