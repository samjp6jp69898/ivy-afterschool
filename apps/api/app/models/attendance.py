"""BACKEND-300：出勤 model（DB-020 student_attendances）。

CHECK（簽到 / 簽退時間成對、簽退不早於簽到、status 與時間一致、``status='leave'`` 必有 leave_id）
只在 DB。``uq_student_attendances_student_date`` 供 ``on_conflict_do_nothing(index_elements=[...])``
使用。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Final, Literal
from uuid import UUID

from sqlalchemy import ForeignKey, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPkMixin
from app.models.leaves import StudentLeave
from app.models.students import Student

AttendanceStatus = Literal["expected", "present", "left", "absent", "leave"]
CheckInSource = Literal["manual", "nfc"]
CheckOutSource = Literal["manual", "pickup", "nfc"]

ATTENDANCE_STATUSES: Final = ("expected", "present", "left", "absent", "leave")
CHECKED_IN_STATUSES: Final = frozenset({"present", "left"})


class StudentAttendance(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "student_attendances"
    # migration 的名稱不是 naming convention 產生的，明確指定
    __table_args__ = (
        UniqueConstraint("student_id", "service_date", name="uq_student_attendances_student_date"),
    )

    student_id: Mapped[UUID] = mapped_column(ForeignKey("students.id", ondelete="RESTRICT"))
    service_date: Mapped[date]
    status: Mapped[AttendanceStatus] = mapped_column(Text, server_default="expected")
    check_in_at: Mapped[datetime | None]
    check_in_source: Mapped[CheckInSource | None] = mapped_column(Text)
    check_out_at: Mapped[datetime | None]
    check_out_source: Mapped[CheckOutSource | None] = mapped_column(Text)
    leave_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("student_leaves.id", ondelete="RESTRICT")
    )
    note: Mapped[str | None]
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("staff_users.id", ondelete="SET NULL")
    )

    student: Mapped[Student] = relationship(lazy="raise")
    leave: Mapped[StudentLeave | None] = relationship(lazy="raise")
