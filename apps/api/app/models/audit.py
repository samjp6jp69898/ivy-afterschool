"""BACKEND-101：稽核 model（DB-006 audit_logs，append-only）。

app_backend 對此表只有 select / insert：本模組不提供任何 update / delete helper，ORM 上誤改屬性後
flush 會被 DB 以權限不足（42501）拒絕。actor_id 為多型參照，無 FK。
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from sqlalchemy import Dialect, Text, TypeDecorator
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPkMixin

ActorType = Literal["staff", "parent", "system", "device"]


class InetText(TypeDecorator[str]):
    """DB 為 inet、Python 端為 str：psycopg 預設讀回 ipaddress 物件，統一轉回字串。"""

    impl = postgresql.INET
    cache_ok = True

    def process_result_value(self, value: Any, dialect: Dialect) -> str | None:
        return None if value is None else str(value)


class AuditLog(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "audit_logs"

    actor_type: Mapped[ActorType] = mapped_column(Text)
    actor_id: Mapped[UUID | None]
    # 例如 exam_score.update、attendance.amend、pickup.override_complete
    action: Mapped[str]
    entity_type: Mapped[str]
    entity_id: Mapped[str | None]
    before: Mapped[dict[str, Any] | None]
    after: Mapped[dict[str, Any] | None]
    ip: Mapped[str | None] = mapped_column(InetText)
    user_agent: Mapped[str | None]
