"""BACKEND-340：請假 models（DB-018 student_leaves、DB-019 student_leave_attachments）。

``ex_student_leaves_no_overlap``（同一學生 active 請假日期區間不可重疊的 gist exclusion constraint）
只存在 DB，ORM 與 schema drift 都不比對；違反時為 SQLSTATE 23P01，由 service 轉成 409。
created_by_* / cancelled_by_* 為多型參照（parent_accounts / staff_users），不建 FK。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Final, Literal
from uuid import UUID

from sqlalchemy import ForeignKey, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPkMixin
from app.models.students import Student

LeaveType = Literal["sick", "personal", "other"]
LeaveStatus = Literal["active", "cancelled"]
LeaveActorType = Literal["parent", "staff"]

LEAVE_TYPE_LABELS: Final = {"sick": "病假", "personal": "事假", "other": "其他"}
EXCLUSION_LEAVE_OVERLAP: Final = "ex_student_leaves_no_overlap"


class StudentLeave(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "student_leaves"

    student_id: Mapped[UUID] = mapped_column(ForeignKey("students.id", ondelete="RESTRICT"))
    leave_type: Mapped[LeaveType] = mapped_column(Text)
    start_date: Mapped[date]
    end_date: Mapped[date]
    reason: Mapped[str | None]
    status: Mapped[LeaveStatus] = mapped_column(Text, server_default="active")
    created_by_type: Mapped[LeaveActorType] = mapped_column(Text)
    created_by_id: Mapped[UUID]
    cancelled_at: Mapped[datetime | None]
    cancelled_by_type: Mapped[LeaveActorType | None] = mapped_column(Text)
    cancelled_by_id: Mapped[UUID | None]

    attachments: Mapped[list[StudentLeaveAttachment]] = relationship(
        lazy="selectin", order_by="StudentLeaveAttachment.created_at"
    )
    student: Mapped[Student] = relationship(lazy="raise")


class StudentLeaveAttachment(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "student_leave_attachments"
    # migration 的名稱不是 naming convention 產生的，明確指定
    __table_args__ = (UniqueConstraint("storage_path", name="uq_student_leave_attachments_path"),)

    leave_id: Mapped[UUID] = mapped_column(ForeignKey("student_leaves.id", ondelete="CASCADE"))
    storage_path: Mapped[str]
    mime_type: Mapped[str]
    size_bytes: Mapped[int]
