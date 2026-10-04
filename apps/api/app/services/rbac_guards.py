"""BACKEND-074：RBAC 守衛（自我提權防護）。移植 ivy ``api/permissions_admin.py::_assert_can_grant``
的精神；去掉 scope 判斷。

- ``assert_valid_permission_codes``：去重排序後回傳；含 ``*`` 或不合法碼 → 422
  ``unknown_permission``（``*`` 只存在 admin 角色的 seed 列，不可經 API 授出）。
- ``assert_can_grant``：授出的碼 ⊄ actor 有效權限 → 403 ``cannot_grant_permissions``，列出超出的碼。
  admin（有效權限 == ALL_PERMISSIONS）自然通過。
- 管理員工帳號的守衛 ``assert_can_manage_staff`` 由 BACKEND-534 加在本檔。
"""

from __future__ import annotations

from collections.abc import Iterable

from app.api.deps import CurrentStaff
from app.core.errors import AppError, ForbiddenError
from app.core.permissions import is_valid_permission


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
