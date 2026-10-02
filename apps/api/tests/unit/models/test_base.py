"""BACKEND-007：SQLAlchemy 宣告式基底、共用欄位 mixin 與 naming convention。"""

from datetime import UTC, datetime

from app.models.base import ArchivableMixin, Base, TimestampMixin, UUIDPkMixin
from sqlalchemy import MetaData, UniqueConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped
from sqlalchemy.schema import CreateTable


class Probe(UUIDPkMixin, TimestampMixin, ArchivableMixin, Base):
    __tablename__ = "probe"
    __table_args__ = (UniqueConstraint("name"),)

    name: Mapped[str]


def _ddl() -> str:
    return str(CreateTable(Probe.__table__).compile(dialect=postgresql.dialect()))


def test_models_base_mixin_ddl() -> None:
    ddl = _ddl()

    assert "id UUID" in ddl
    assert "DEFAULT gen_random_uuid()" in ddl
    assert "created_at TIMESTAMP WITH TIME ZONE NOT NULL" in ddl
    assert "updated_at TIMESTAMP WITH TIME ZONE NOT NULL" in ddl
    assert "archived_at TIMESTAMP WITH TIME ZONE" in ddl
    assert "PRIMARY KEY (id)" in ddl


def test_models_base_naming_convention() -> None:
    names = {c.name for c in Probe.__table__.constraints}

    assert "uq_probe_name" in names
    assert "pk_probe" in names


def test_models_base_archivable() -> None:
    probe = Probe(name="王小明")

    assert probe.is_archived is False
    probe.archived_at = datetime(2026, 10, 2, 8, 0, tzinfo=UTC)
    assert probe.is_archived is True


def test_models_base_import() -> None:
    from app.models import Base as ExportedBase

    assert isinstance(ExportedBase.metadata, MetaData)
    assert ExportedBase is Base
