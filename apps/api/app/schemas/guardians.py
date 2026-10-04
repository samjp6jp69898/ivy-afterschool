"""BACKEND-167：監護人 schemas（欄位限制對齊 DB-016 CHECK）。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.models.parents import GuardianRelation
from app.schemas.common import OutModel, RequestModel, UpdateModel


class GuardianCreateIn(RequestModel):
    name: str = Field(min_length=1, max_length=50)
    relation: GuardianRelation
    phone: str | None = Field(default=None, max_length=20)
    is_primary: bool = False
    can_pickup: bool = True
    receives_notifications: bool = True


class GuardianUpdateIn(UpdateModel):
    nullable_fields = frozenset({"phone"})

    name: str | None = Field(default=None, min_length=1, max_length=50)
    relation: GuardianRelation | None = None
    phone: str | None = Field(default=None, max_length=20)
    is_primary: bool | None = None
    can_pickup: bool | None = None
    receives_notifications: bool | None = None


class GuardianBindingOut(OutModel):
    status: Literal["bound", "code_issued", "unbound"]
    parent_display_name: str | None = None  # bound 時
    code_expires_at: datetime | None = None  # code_issued 時：最新未使用且未過期碼的到期時間


class GuardianOut(OutModel):
    id: UUID
    student_id: UUID
    name: str
    relation: GuardianRelation
    phone: str | None
    is_primary: bool
    can_pickup: bool
    receives_notifications: bool
    binding: GuardianBindingOut


class BindingCodeOut(OutModel):
    guardian_id: UUID
    code: str  # 明碼，只在產生當下回傳
    expires_at: datetime
