"""BACKEND-131：學生 model（DB-014 students）。

欄位與 migration 完全一致（BACKEND-024 drift 把關）；CHECK 與 partial index 只在 migration。
``school_class`` 是就讀國小的班級文字（例如「三年二班」），安親班班級的關係屬性為 ``class_``。
敏感欄位（id_number_enc、health_note_enc）只存密文，不提供自動解密的 property：解密只在有
``students:sensitive`` 的路徑由 service 呼叫 BACKEND-009。
``guardians``（BACKEND-132）為 lazy='raise'，需要時明確 ``selectinload(Student.guardians)``，
避免 N+1。
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Literal
from uuid import UUID

from sqlalchemy import ForeignKey, Index, LargeBinary, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import ArchivableMixin, Base, TimestampMixin, UUIDPkMixin
from app.models.classes import SchoolClass
from app.models.reference import School

if TYPE_CHECKING:
    from app.models.parents import Guardian

Gender = Literal["male", "female", "other"]
StudentStatus = Literal["active", "suspended", "withdrawn"]


class Student(UUIDPkMixin, TimestampMixin, ArchivableMixin, Base):
    __tablename__ = "students"
    __table_args__ = (
        # 查重用；唯一性含已封存 / 退班學生
        Index(
            "uq_students_id_number_hmac",
            "id_number_hmac",
            unique=True,
            postgresql_where=text("id_number_hmac is not null"),
        ),
    )

    student_no: Mapped[str] = mapped_column(unique=True)
    name: Mapped[str]
    gender: Mapped[Gender | None] = mapped_column(Text)
    birthday: Mapped[date | None]
    grade_level: Mapped[int]
    school_id: Mapped[UUID | None] = mapped_column(ForeignKey("schools.id", ondelete="RESTRICT"))
    school_class: Mapped[str | None]
    class_id: Mapped[UUID | None] = mapped_column(ForeignKey("classes.id", ondelete="SET NULL"))
    status: Mapped[StudentStatus] = mapped_column(Text, server_default="active")
    enrolled_on: Mapped[date | None]
    withdrawn_on: Mapped[date | None]
    photo_path: Mapped[str | None]
    id_number_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    id_number_hmac: Mapped[str | None]
    health_note_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    note: Mapped[str | None]

    school: Mapped[School | None] = relationship(lazy="joined")
    class_: Mapped[SchoolClass | None] = relationship(lazy="joined")
    guardians: Mapped[list[Guardian]] = relationship(back_populates="student", lazy="raise")
