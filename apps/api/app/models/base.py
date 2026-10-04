"""BACKEND-007：SQLAlchemy 2 宣告式基底與共用欄位 mixin。

- naming convention 與 Alembic revision 的約束命名一致（uq_<table>_<col> 等），
  schema drift 測試（INFRA-020）才對得上。
- enum 欄位一律 ``Text`` + Python ``Literal`` 型別註記，不用 SQLAlchemy Enum（DB 為 text + CHECK）。
- 加密欄位型別為 ``LargeBinary``（bytea）；加解密在 service 層呼叫 BACKEND-009，
  不寫 TypeDecorator，避免在未授權路徑意外解密。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar
from uuid import UUID

from sqlalchemy import DateTime, FetchedValue, MetaData, Text, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map: ClassVar[dict[Any, Any]] = {
        # migration 的字串欄位一律 text（長度由 CHECK 約束把關），不用 varchar
        str: Text,
        datetime: DateTime(timezone=True),
        UUID: postgresql.UUID(as_uuid=True),
        dict[str, Any]: postgresql.JSONB,
        list[str]: postgresql.ARRAY(Text),
    }


class UUIDPkMixin:
    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=text("now()"))
    # updated_at 由 DB trigger 維護（DB-002），ORM 端只宣告「寫入後由 DB 產生」
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=text("now()"), server_onupdate=FetchedValue()
    )


class ArchivableMixin:
    archived_at: Mapped[datetime | None]

    @hybrid_property
    def is_archived(self) -> bool:
        return self.archived_at is not None

    @is_archived.inplace.expression
    @classmethod
    def _is_archived_expression(cls) -> Any:
        return cls.archived_at.is_not(None)
