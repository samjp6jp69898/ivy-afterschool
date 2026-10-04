"""BACKEND-104：稽核紀錄 schemas。"""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.audit import AuditLogOut, AuditLogQuery


def test_audit_schemas_actor_type() -> None:
    with pytest.raises(ValidationError):
        AuditLogQuery(actor_type="robot")
    for value in ("staff", "parent", "system", "device"):
        assert AuditLogQuery(actor_type=value).actor_type == value


def test_audit_schemas_date_range() -> None:
    with pytest.raises(ValidationError):
        AuditLogQuery(date_from=date(2026, 9, 2), date_to=date(2026, 9, 1))
    q = AuditLogQuery(date_from=date(2026, 9, 1), date_to=date(2026, 9, 1))
    assert q.date_from == q.date_to == date(2026, 9, 1)
    assert AuditLogQuery(date_from=date(2026, 9, 1)).date_to is None


def test_audit_schemas_query_limits_and_extra() -> None:
    with pytest.raises(ValidationError):
        AuditLogQuery(action="a" * 101)
    assert AuditLogQuery(entity_id="e" * 100).entity_id == "e" * 100
    with pytest.raises(ValidationError):
        AuditLogQuery(actor_id="not-a-uuid")
    with pytest.raises(ValidationError) as exc:
        AuditLogQuery.model_validate({"foo": 1})
    assert exc.value.errors()[0]["type"] == "extra_forbidden"


def test_audit_schemas_out_fields() -> None:
    out = AuditLogOut(
        id=uuid4(),
        created_at=datetime(2026, 9, 1, tzinfo=UTC),
        actor_type="system",
        actor_id=None,
        actor_name=None,
        action="student.update",
        entity_type="student",
        entity_id=None,
        before=None,
        after={"name": "王小明"},
        ip=None,
        user_agent=None,
    )
    assert out.after == {"name": "王小明"}
    assert set(AuditLogOut.model_fields) == {
        "id", "created_at", "actor_type", "actor_id", "actor_name", "action",
        "entity_type", "entity_id", "before", "after", "ip", "user_agent",
    }  # fmt: skip
