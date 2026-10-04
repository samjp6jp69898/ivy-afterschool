"""BACKEND-086：員工帳號模組 schemas。

- username 格式 ``^[A-Za-z0-9][A-Za-z0-9._-]{2,31}$``（service 轉小寫，DB CHECK 要求小寫）。
- email 以 DB CHECK ``^[^@\\s]+@[^@\\s]+$`` 為準（專案沒有 email-validator 相依，不用 EmailStr）。
- 臨時密碼只在建立 / 重設時由系統產生並回傳一次，建立請求不接受 client 指定密碼。
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import Field, StringConstraints

from app.schemas.auth import RoleBrief
from app.schemas.common import OutModel, RequestModel, UpdateModel

USERNAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{2,31}$"
EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+$"

Email = Annotated[str, StringConstraints(pattern=EMAIL_PATTERN, max_length=254)]
PermissionCodes = Annotated[list[str], Field(max_length=50)]


class StaffUserCreateIn(RequestModel):
    username: str = Field(pattern=USERNAME_PATTERN)
    display_name: str = Field(min_length=1, max_length=50)
    phone: str | None = Field(default=None, max_length=20)
    email: Email | None = None
    role_id: UUID
    extra_permissions: PermissionCodes = Field(default_factory=list)
    revoked_permissions: PermissionCodes = Field(default_factory=list)


class StaffUserUpdateIn(UpdateModel):
    """username 不可改；phone / email 給 null 代表清除。"""

    nullable_fields = frozenset({"phone", "email"})

    display_name: str | None = Field(default=None, min_length=1, max_length=50)
    phone: str | None = Field(default=None, max_length=20)
    email: Email | None = None
    role_id: UUID | None = None
    extra_permissions: PermissionCodes | None = None
    revoked_permissions: PermissionCodes | None = None


class StaffUserListQuery(RequestModel):
    q: str | None = Field(default=None, max_length=50)
    role_id: UUID | None = None
    is_active: bool | None = None


class StaffUserOut(OutModel):
    id: UUID
    username: str
    display_name: str
    phone: str | None
    email: str | None
    role: RoleBrief
    extra_permissions: list[str]
    revoked_permissions: list[str]
    effective_permissions: list[str]  # 已排序
    is_active: bool
    must_change_password: bool
    last_login_at: datetime | None
    created_at: datetime


class StaffUserCreatedOut(OutModel):
    user: StaffUserOut
    temp_password: str  # 只在建立時回傳一次


class TempPasswordOut(OutModel):
    temp_password: str


class StaffOptionOut(OutModel):
    """班級負責員工指派下拉用，不含帳號與權限資訊。"""

    id: UUID
    display_name: str
