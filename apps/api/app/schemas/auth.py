"""BACKEND-039：認證模組 schemas（員工登入 / 改密碼 / me，家長 LIFF / 綁定）。

- request model 繼承 ``RequestModel``（extra='forbid'、去頭尾空白、拒 NUL）；密碼欄位以
  ``StringConstraints(strip_whitespace=False)`` 保留原文（空白也是密碼的一部分）。
- 密碼強度由 BACKEND-032 在 service 驗證（錯誤訊息一致），schema 只擋長度。
- 綁定碼允許輸入含連字號與空白，正規化在 service。
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StringConstraints

from app.schemas.common import OutModel, RequestModel
from app.schemas.parent_children import ParentMeOut

Password = Annotated[str, StringConstraints(strip_whitespace=False, min_length=1, max_length=128)]


class StaffLoginIn(RequestModel):
    username: str = Field(min_length=1, max_length=32)
    password: Password


class ChangePasswordIn(RequestModel):
    current_password: Password
    new_password: Password


class RoleBrief(OutModel):
    id: UUID
    code: str
    name: str


class StaffMeOut(OutModel):
    id: UUID
    username: str
    display_name: str
    role: RoleBrief
    permissions: list[str]  # 已排序
    must_change_password: bool


class StaffAuthOut(OutModel):
    """login / refresh / change-password 的回應。"""

    user: StaffMeOut


class LiffLoginIn(RequestModel):
    id_token: str = Field(min_length=1, max_length=4096)


class BindIn(RequestModel):
    code: str = Field(min_length=4, max_length=20)


class LiffLoginOut(OutModel):
    status: Literal["ok", "needs_binding"]
    parent: ParentMeOut | None
    name_hint: str | None  # needs_binding 時帶 LINE 暱稱


class ParentAuthOut(OutModel):
    """bind / refresh 的回應。"""

    parent: ParentMeOut


class MessageOut(OutModel):
    message: str
