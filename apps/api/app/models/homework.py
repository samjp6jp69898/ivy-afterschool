"""BACKEND-370：作業進度 models（DB-021 homework_items、DB-022 homework_daily_progress）。

``ready_eta`` 為台北當地時間的 ``time``（不含時區）。``ck_homework_daily_progress_eta_audit`` 要求
有 ready_eta 時必須同時有 eta_updated_at（CHECK 只在 DB）。
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Literal
from uuid import UUID

from sqlalchemy import ForeignKey, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPkMixin
from app.models.reference import Subject

HomeworkItemStatus = Literal["todo", "doing", "correcting", "done"]
OverallStatus = Literal["not_started", "in_progress", "done"]


class HomeworkItem(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "homework_items"

    student_id: Mapped[UUID] = mapped_column(ForeignKey("students.id", ondelete="RESTRICT"))
    service_date: Mapped[date]
    subject_id: Mapped[UUID | None] = mapped_column(ForeignKey("subjects.id", ondelete="SET NULL"))
    title: Mapped[str]
    status: Mapped[HomeworkItemStatus] = mapped_column(Text, server_default="todo")
    sort_order: Mapped[int] = mapped_column(server_default="0")
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("staff_users.id", ondelete="SET NULL")
    )

    subject: Mapped[Subject | None] = relationship(lazy="joined")


class HomeworkDailyProgress(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "homework_daily_progress"
    # migration 的名稱不是 naming convention 產生的，明確指定
    __table_args__ = (
        UniqueConstraint(
            "student_id", "service_date", name="uq_homework_daily_progress_student_date"
        ),
    )

    student_id: Mapped[UUID] = mapped_column(ForeignKey("students.id", ondelete="RESTRICT"))
    service_date: Mapped[date]
    overall_status: Mapped[OverallStatus] = mapped_column(Text, server_default="not_started")
    ready_eta: Mapped[time | None]
    eta_updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("staff_users.id", ondelete="SET NULL")
    )
    eta_updated_at: Mapped[datetime | None]
    note: Mapped[str | None]
