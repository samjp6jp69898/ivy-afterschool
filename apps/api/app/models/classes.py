"""BACKEND-130：班級 models（DB-012 classes、DB-013 class_staff）。

Python 類別名用 ``SchoolClass``（避免與 ``class`` 關鍵字混淆），``__tablename__ = 'classes'``。
欄位與 migration 完全一致（BACKEND-024 drift 把關）；CHECK 與 partial index 只在 migration。
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from sqlalchemy import ForeignKey, Integer, Text, UniqueConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.account import StaffUser
from app.models.base import ArchivableMixin, Base, TimestampMixin, UUIDPkMixin

ClassStaffRole = Literal["lead", "assistant"]


class SchoolClass(UUIDPkMixin, TimestampMixin, ArchivableMixin, Base):
    __tablename__ = "classes"

    name: Mapped[str]
    grade_levels: Mapped[list[int]] = mapped_column(postgresql.ARRAY(Integer))
    # 民國學年度，例如 115
    academic_year: Mapped[int]
    sort_order: Mapped[int] = mapped_column(server_default="0")

    staff_links: Mapped[list[ClassStaff]] = relationship(
        back_populates="school_class", lazy="selectin", cascade="all, delete-orphan"
    )


class ClassStaff(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "class_staff"
    # migration 的名稱不是 naming convention 產生的，明確指定
    __table_args__ = (
        UniqueConstraint("class_id", "staff_user_id", name="uq_class_staff_class_staff"),
    )

    class_id: Mapped[UUID] = mapped_column(ForeignKey("classes.id", ondelete="CASCADE"))
    staff_user_id: Mapped[UUID] = mapped_column(ForeignKey("staff_users.id", ondelete="CASCADE"))
    role: Mapped[ClassStaffRole] = mapped_column(Text, server_default="assistant")

    school_class: Mapped[SchoolClass] = relationship(back_populates="staff_links")
    staff: Mapped[StaffUser] = relationship(lazy="joined")
