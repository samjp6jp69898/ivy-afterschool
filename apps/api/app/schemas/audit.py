"""BACKEND-104：稽核紀錄 schemas。"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Self
from uuid import UUID

from pydantic import Field, model_validator

from app.models.audit import ActorType
from app.schemas.common import OutModel, RequestModel


class AuditLogQuery(RequestModel):
    action: str | None = Field(default=None, max_length=100)
    action_prefix: str | None = Field(default=None, max_length=100)
    entity_type: str | None = Field(default=None, max_length=100)
    entity_id: str | None = Field(default=None, max_length=100)
    actor_type: ActorType | None = None
    actor_id: UUID | None = None
    date_from: date | None = None
    date_to: date | None = None

    @model_validator(mode="after")
    def _check_date_range(self) -> Self:
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("開始日不可晚於結束日")
        return self


class AuditLogOut(OutModel):
    id: UUID
    created_at: datetime
    actor_type: ActorType
    actor_id: UUID | None
    actor_name: str | None
    action: str
    entity_type: str | None
    entity_id: str | None
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    ip: str | None
    user_agent: str | None
