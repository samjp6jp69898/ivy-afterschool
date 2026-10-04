"""BACKEND-100：系統設定與參考資料 models（DB-007 system_settings、DB-008 subjects、
DB-009 exam_types、DB-010 schools、DB-011 closed_days）。

欄位與 migration 完全一致（BACKEND-024 drift 把關）。Subject / ExamType / School 的名稱唯一是 DB 的
functional unique index（``lower(btrim(name))``），model 端宣告同名索引但 drift 不比對 index
（INFRA-020 規則），service 以 IntegrityError 轉譯。
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import ForeignKey, Index, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPkMixin


class SystemSetting(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(unique=True)
    value: Mapped[dict[str, Any]]
    is_secret: Mapped[bool] = mapped_column(server_default="false")
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("staff_users.id", ondelete="SET NULL")
    )


class Subject(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "subjects"
    __table_args__ = (Index("uq_subjects_name", text("lower(btrim(name))"), unique=True),)

    name: Mapped[str]
    sort_order: Mapped[int] = mapped_column(server_default="0")
    is_active: Mapped[bool] = mapped_column(server_default="true")


class ExamType(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "exam_types"
    __table_args__ = (Index("uq_exam_types_name", text("lower(btrim(name))"), unique=True),)

    name: Mapped[str]
    sort_order: Mapped[int] = mapped_column(server_default="0")
    is_active: Mapped[bool] = mapped_column(server_default="true")


class School(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "schools"
    __table_args__ = (Index("uq_schools_name", text("lower(btrim(name))"), unique=True),)

    name: Mapped[str]
    short_name: Mapped[str | None]
    is_active: Mapped[bool] = mapped_column(server_default="true")


class ClosedDay(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "closed_days"

    date: Mapped[date] = mapped_column(unique=True)
    reason: Mapped[str | None]
