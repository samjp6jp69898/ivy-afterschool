"""BACKEND-074：RBAC 守衛（自我提權防護）。移植 ivy ``api/permissions_admin.py::_assert_can_grant``
的精神；去掉 scope 判斷。

- ``assert_valid_permission_codes``：去重排序後回傳；含 ``*`` 或不合法碼 → 422
  ``unknown_permission``（``*`` 只存在 admin 角色的 seed 列，不可經 API 授出）。
- ``assert_can_grant``：授出的碼 ⊄ actor 有效權限 → 403 ``cannot_grant_permissions``，列出超出的碼。
  admin（有效權限 == ALL_PERMISSIONS）自然通過。

BACKEND-534：``assert_can_manage_staff``（移植 ivy ``api/auth.py::_assert_can_manage_user`` 第 4 點
「目標最終權限 ⊆ caller 權限」；去掉 scope / super_admin 旗標）。目標帳號的有效權限 ⊄ actor 有效
權限 → 403 ``cannot_manage_staff``（主任不能修改 / 重設密碼 / 停用 / 啟用 admin）；admin 自然通過、
目標為空集合通過。使用者：BACKEND-090 / 091 / 092 / 521。

BACKEND-075：``assert_admin_capabilities_retained``（移植 ivy
``api/permissions_admin.py::_assert_roles_manage_retained``；去掉 tenant，判準擴充為 roles:write 與
staff:write）。角色權限變更 / 刪除、員工改角色 / 改個別權限 / 停用之後、commit 之前呼叫（query 會
autoflush）：所有啟用員工的有效權限中沒有人持有 roles:write → 409 ``last_role_manager``；沒有人持有
staff:write → 409 ``last_staff_manager``。以帳號為準（留著沒人用的角色不算），停用帳號不算。
查詢帶 ``populate_existing``（BACKEND-079 改寫）：呼叫端先取 ``rbac:admin_retained`` advisory lock
再呼叫，鎖後必須讀到其他交易已 commit 的最新值；request session 多半已載入 actor 的員工與角色
（``get_current_staff``），不覆寫 identity map 會讀到舊值，兩筆各降權一位管理者的交易會同時通過。
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import CurrentStaff
from app.core.errors import AppError, ConflictError, ForbiddenError
from app.core.permissions import Permission, is_valid_permission, resolve_effective_permissions
from app.models.account import StaffUser


def assert_valid_permission_codes(codes: Iterable[str]) -> list[str]:
    unique = sorted({str(code) for code in codes})
    invalid = [code for code in unique if not is_valid_permission(code)]
    if invalid:
        raise AppError(
            "unknown_permission", "包含無效的權限碼", status=422, details={"invalid": invalid}
        )
    return unique


def assert_can_grant(actor: CurrentStaff, codes: Iterable[str]) -> None:
    beyond = sorted({str(code) for code in codes} - actor.permissions)
    if beyond:
        raise ForbiddenError(
            "不可授出自己沒有的權限",
            code="cannot_grant_permissions",
            details={"permissions": beyond},
        )


def assert_can_manage_staff(actor: CurrentStaff, target_effective: frozenset[str]) -> None:
    if not target_effective <= actor.permissions:
        raise ForbiddenError("無法管理權限比您大的帳號", code="cannot_manage_staff")


def assert_admin_capabilities_retained(session: Session) -> None:
    # StaffUser.role 為 lazy='joined'：一次查詢同時帶出角色，不 N+1；populate_existing 讓 identity
    # map 中已載入的員工 / 角色（例如 actor 自己的）以 DB 最新值覆寫
    active_staff = (
        session.execute(
            select(StaffUser)
            .where(StaffUser.is_active.is_(True))
            .execution_options(populate_existing=True)
        )
        .unique()
        .scalars()
    )
    has_roles_write = has_staff_write = False
    for staff in active_staff:
        effective = resolve_effective_permissions(
            staff.role.permissions, staff.extra_permissions, staff.revoked_permissions
        )
        has_roles_write = has_roles_write or Permission.ROLES_WRITE in effective
        has_staff_write = has_staff_write or Permission.STAFF_WRITE in effective
        if has_roles_write and has_staff_write:
            return
    if not has_roles_write:
        raise ConflictError("last_role_manager", "此變更會讓系統沒有任何可管理角色權限的啟用帳號")
    raise ConflictError("last_staff_manager", "此變更會讓系統沒有任何可管理員工帳號的啟用帳號")
