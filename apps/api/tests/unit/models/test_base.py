"""BACKEND-007：SQLAlchemy 宣告式基底、共用欄位 mixin 與 naming convention。"""

from datetime import UTC, datetime

from sqlalchemy import MetaData, Table, UniqueConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped
from sqlalchemy.schema import CreateTable

from app.models.base import ArchivableMixin, Base, TimestampMixin, UUIDPkMixin


class Probe(UUIDPkMixin, TimestampMixin, ArchivableMixin, Base):
    __tablename__ = "probe"
    __table_args__ = (UniqueConstraint("name"),)

    name: Mapped[str]


def _probe_table() -> Table:
    return Base.metadata.tables["probe"]


def _ddl() -> str:
    # DDLElement.compile 在 SQLAlchemy 的型別標註中未加註
    compiled = CreateTable(_probe_table()).compile(dialect=postgresql.dialect())  # type: ignore[no-untyped-call]
    return str(compiled)


def test_models_base_mixin_ddl() -> None:
    ddl = _ddl()

    assert "id UUID" in ddl
    assert "DEFAULT gen_random_uuid()" in ddl
    # postgresql dialect 把 DEFAULT 放在 NOT NULL 前面
    assert "created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL" in ddl
    assert "updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL" in ddl
    assert "archived_at TIMESTAMP WITH TIME ZONE" in ddl
    assert "PRIMARY KEY (id)" in ddl


def test_models_base_naming_convention() -> None:
    names = {c.name for c in _probe_table().constraints}

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
