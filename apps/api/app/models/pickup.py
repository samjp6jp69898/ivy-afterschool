"""BACKEND-401：接送 models（DB-023 pickup_persons、DB-024 授權、DB-025 pickup_requests）。

``uq_pickup_requests_one_open``（同一學生同一天只能有一筆非終態請求）是 DB 的 partial unique index，
ORM 與 schema drift 不比對；違反時為 SQLSTATE 23505，由 service 轉成 409。其餘 CHECK
（鎖定、驗證、回覆、完成狀態一致性）也只在 DB。requested_by_* 為多型參照，不建 FK。
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Final, Literal
from uuid import UUID

from sqlalchemy import ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import ArchivableMixin, Base, TimestampMixin, UUIDPkMixin

AuthorizationStatus = Literal["active", "completed", "cancelled"]
VerificationMethod = Literal["code", "visual_match", "override"]
RequestSource = Literal["parent", "staff", "proxy"]
RequesterType = Literal["parent", "staff"]
RequestStatus = Literal["pending", "acknowledged", "arrived", "completed", "cancelled", "expired"]
HomeworkStatusAtRequest = Literal["not_started", "in_progress", "done"]
ReplySource = Literal["auto", "staff"]
CompletionMethod = Literal["guardian", "code", "visual_match", "override"]

OPEN_STATUSES: Final = frozenset({"pending", "acknowledged", "arrived"})
TERMINAL_STATUSES: Final = frozenset({"completed", "cancelled", "expired"})
UQ_ONE_OPEN: Final = "uq_pickup_requests_one_open"


class PickupPerson(UUIDPkMixin, TimestampMixin, ArchivableMixin, Base):
    __tablename__ = "pickup_persons"

    student_id: Mapped[UUID] = mapped_column(ForeignKey("students.id", ondelete="RESTRICT"))
    name: Mapped[str]
    relation: Mapped[str]
    phone: Mapped[str]
    photo_path: Mapped[str | None]
    created_by_parent_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("parent_accounts.id", ondelete="SET NULL")
    )


class PickupAuthorization(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "pickup_authorizations"

    student_id: Mapped[UUID] = mapped_column(ForeignKey("students.id", ondelete="RESTRICT"))
    service_date: Mapped[date]
    pickup_person_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("pickup_persons.id", ondelete="SET NULL")
    )
    proxy_name: Mapped[str]
    proxy_phone: Mapped[str]
    code_hash: Mapped[str]
    code_last4: Mapped[str]
    code_attempts: Mapped[int] = mapped_column(server_default="0")
    code_locked_at: Mapped[datetime | None]
    status: Mapped[AuthorizationStatus] = mapped_column(Text, server_default="active")
    verified_at: Mapped[datetime | None]
    verified_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("staff_users.id", ondelete="SET NULL")
    )
    verification_method: Mapped[VerificationMethod | None] = mapped_column(Text)
    created_by_parent_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("parent_accounts.id", ondelete="SET NULL")
    )

    pickup_person: Mapped[PickupPerson | None] = relationship(lazy="joined")


class PickupRequest(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "pickup_requests"

    student_id: Mapped[UUID] = mapped_column(ForeignKey("students.id", ondelete="RESTRICT"))
    service_date: Mapped[date]
    source: Mapped[RequestSource] = mapped_column(Text)
    requested_by_type: Mapped[RequesterType] = mapped_column(Text)
    requested_by_id: Mapped[UUID]
    expected_arrival_at: Mapped[datetime | None]
    status: Mapped[RequestStatus] = mapped_column(Text, server_default="pending")
    homework_status_at_request: Mapped[HomeworkStatusAtRequest | None] = mapped_column(Text)
    reply_ready_eta: Mapped[time | None]
    reply_message: Mapped[str | None]
    reply_source: Mapped[ReplySource | None] = mapped_column(Text)
    replied_at: Mapped[datetime | None]
    replied_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("staff_users.id", ondelete="RESTRICT")
    )
    arrived_at: Mapped[datetime | None]
    completed_at: Mapped[datetime | None]
    completed_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("staff_users.id", ondelete="SET NULL")
    )
    picked_up_by_guardian_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("guardians.id", ondelete="RESTRICT")
    )
    picked_up_by_authorization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("pickup_authorizations.id", ondelete="RESTRICT")
    )
    completion_method: Mapped[CompletionMethod | None] = mapped_column(Text)
    cancelled_at: Mapped[datetime | None]
    cancel_reason: Mapped[str | None]
