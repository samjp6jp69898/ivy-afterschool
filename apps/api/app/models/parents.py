"""BACKEND-132：家長 models（DB-015 parent_accounts、DB-016 guardians、
DB-017 parent_binding_codes）。

欄位與 migration 完全一致（BACKEND-024 drift 把關）；CHECK 與 partial unique index 只在 migration
（同一學生未封存的主要聯絡人唯一、同一家長帳號不重複綁同一學生），service 以 IntegrityError 轉譯。
``Student.guardians``（lazy='raise'）在 ``app/models/students.py`` 宣告，與 ``Guardian.student``
互為 back_populates。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import ArchivableMixin, Base, TimestampMixin, UUIDPkMixin
from app.models.students import Student

ParentStatus = Literal["active", "disabled"]
GuardianRelation = Literal["father", "mother", "grandparent", "other"]


class ParentAccount(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "parent_accounts"

    line_user_id: Mapped[str] = mapped_column(unique=True)
    display_name: Mapped[str | None]
    picture_url: Mapped[str | None]
    phone: Mapped[str | None]
    status: Mapped[ParentStatus] = mapped_column(Text, server_default="active")
    token_version: Mapped[int] = mapped_column(server_default="0")
    last_login_at: Mapped[datetime | None]


class Guardian(UUIDPkMixin, TimestampMixin, ArchivableMixin, Base):
    __tablename__ = "guardians"

    student_id: Mapped[UUID] = mapped_column(ForeignKey("students.id", ondelete="RESTRICT"))
    # 綁定 LINE 後填入；家長帳號刪除時解除綁定
    parent_account_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("parent_accounts.id", ondelete="SET NULL")
    )
    name: Mapped[str]
    relation: Mapped[GuardianRelation] = mapped_column(Text)
    phone: Mapped[str | None]
    is_primary: Mapped[bool] = mapped_column(server_default="false")
    can_pickup: Mapped[bool] = mapped_column(server_default="true")
    receives_notifications: Mapped[bool] = mapped_column(server_default="true")

    student: Mapped[Student] = relationship(back_populates="guardians", lazy="joined")
    parent_account: Mapped[ParentAccount | None] = relationship(lazy="joined")


class ParentBindingCode(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "parent_binding_codes"

    guardian_id: Mapped[UUID] = mapped_column(ForeignKey("guardians.id", ondelete="CASCADE"))
    # 綁定碼的 HMAC-SHA256 hex（BACKEND-010），DB 不存明文
    code_hash: Mapped[str] = mapped_column(unique=True)
    expires_at: Mapped[datetime]
    used_at: Mapped[datetime | None]
    created_by: Mapped[UUID] = mapped_column(ForeignKey("staff_users.id", ondelete="RESTRICT"))
