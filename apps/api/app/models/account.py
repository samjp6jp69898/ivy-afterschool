"""BACKEND-030：帳號模組 models（DB-003 roles、DB-004 staff_users、DB-005 refresh_tokens）。

欄位、型別、nullable、FK、unique 與 migration 完全一致（BACKEND-024 drift 測試把關）；
CHECK 約束與索引只在 migration，ORM 不重複宣告。enum 欄位依 BACKEND-007 慣例為 ``Text`` +
``Literal`` 型別註記。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPkMixin

SubjectType = Literal["staff", "parent"]


class Role(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "roles"

    code: Mapped[str] = mapped_column(unique=True)
    name: Mapped[str]
    description: Mapped[str | None]
    permissions: Mapped[list[str]] = mapped_column(server_default="{}")
    is_system: Mapped[bool] = mapped_column(server_default="false")

    # lazy='raise'：要列出角色底下的員工時必須明確 selectinload，避免 N+1
    staff_users: Mapped[list[StaffUser]] = relationship(back_populates="role", lazy="raise")


class StaffUser(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "staff_users"

    username: Mapped[str] = mapped_column(unique=True)
    password_hash: Mapped[str]
    display_name: Mapped[str]
    phone: Mapped[str | None]
    email: Mapped[str | None]
    role_id: Mapped[UUID] = mapped_column(ForeignKey("roles.id", ondelete="RESTRICT"))
    extra_permissions: Mapped[list[str]] = mapped_column(server_default="{}")
    revoked_permissions: Mapped[list[str]] = mapped_column(server_default="{}")
    is_active: Mapped[bool] = mapped_column(server_default="true")
    token_version: Mapped[int] = mapped_column(server_default="0")
    last_login_at: Mapped[datetime | None]
    must_change_password: Mapped[bool] = mapped_column(server_default="true")

    role: Mapped[Role] = relationship(back_populates="staff_users", lazy="joined")


class RefreshToken(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "refresh_tokens"

    subject_type: Mapped[SubjectType] = mapped_column(Text)
    # 多型參照 staff_users / parent_accounts，無 FK（DB-005）
    subject_id: Mapped[UUID]
    family_id: Mapped[UUID]
    token_hash: Mapped[str] = mapped_column(unique=True)
    expires_at: Mapped[datetime]
    revoked_at: Mapped[datetime | None]
    # 輪替後指向下一棒；非 null 的 token 再次出現即為重用
    replaced_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("refresh_tokens.id", ondelete="SET NULL")
    )
