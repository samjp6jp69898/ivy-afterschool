"""BACKEND-450：成績 models（DB-026 exams、DB-027 exam_subjects、DB-028 exam_scores）。

分數不得超過該科滿分（trigger，SQLSTATE 23514）、缺考不可同時有分數、只能為考試有設定的科目登分
（複合 FK ``fk_exam_scores_exam_subject``，on delete cascade）都由 DB 把關；ORM 只宣告結構。
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from sqlalchemy import ForeignKey, ForeignKeyConstraint, Numeric, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPkMixin
from app.models.classes import SchoolClass
from app.models.reference import ExamType, Subject

ExamStatus = Literal["draft", "published"]


class Exam(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "exams"

    name: Mapped[str]
    exam_type_id: Mapped[UUID] = mapped_column(ForeignKey("exam_types.id", ondelete="RESTRICT"))
    exam_date: Mapped[date]
    grade_level: Mapped[int | None]
    class_id: Mapped[UUID | None] = mapped_column(ForeignKey("classes.id", ondelete="RESTRICT"))
    status: Mapped[ExamStatus] = mapped_column(Text, server_default="draft")
    published_at: Mapped[datetime | None]
    published_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("staff_users.id", ondelete="SET NULL")
    )
    note: Mapped[str | None]

    exam_type: Mapped[ExamType] = relationship(lazy="joined")
    school_class: Mapped[SchoolClass | None] = relationship(lazy="joined")
    subjects: Mapped[list[ExamSubject]] = relationship(
        lazy="selectin", order_by="ExamSubject.sort_order"
    )


class ExamSubject(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "exam_subjects"
    # migration 的名稱不是 naming convention 產生的，明確指定
    __table_args__ = (
        UniqueConstraint("exam_id", "subject_id", name="uq_exam_subjects_exam_subject"),
    )

    exam_id: Mapped[UUID] = mapped_column(ForeignKey("exams.id", ondelete="CASCADE"))
    subject_id: Mapped[UUID] = mapped_column(ForeignKey("subjects.id", ondelete="RESTRICT"))
    full_score: Mapped[Decimal] = mapped_column(Numeric(6, 2), server_default="100")
    sort_order: Mapped[int] = mapped_column(server_default="0")

    subject: Mapped[Subject] = relationship(lazy="joined")


class ExamScore(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "exam_scores"
    # 唯一約束供 on_conflict_do_update(index_elements=[exam_id, student_id, subject_id]) 使用；
    # subject_id 沒有單獨的 FK，只經複合 FK 對 exam_subjects（名稱不是 naming convention 產生的）
    __table_args__ = (
        UniqueConstraint(
            "exam_id", "student_id", "subject_id", name="uq_exam_scores_exam_student_subject"
        ),
        ForeignKeyConstraint(
            ["exam_id", "subject_id"],
            ["exam_subjects.exam_id", "exam_subjects.subject_id"],
            ondelete="CASCADE",
            name="fk_exam_scores_exam_subject",
        ),
    )

    exam_id: Mapped[UUID] = mapped_column(ForeignKey("exams.id", ondelete="CASCADE"))
    student_id: Mapped[UUID] = mapped_column(ForeignKey("students.id", ondelete="RESTRICT"))
    subject_id: Mapped[UUID]
    score: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    is_absent: Mapped[bool] = mapped_column(server_default="false")
    note: Mapped[str | None]
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("staff_users.id", ondelete="SET NULL")
    )
