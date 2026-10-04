"""BACKEND-167：監護人 schemas。"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.guardians import (
    BindingCodeOut,
    GuardianBindingOut,
    GuardianCreateIn,
    GuardianOut,
    GuardianUpdateIn,
)


def test_guardian_schemas_relation() -> None:
    with pytest.raises(ValidationError):
        GuardianCreateIn(name="王媽媽", relation="aunt")
    g = GuardianCreateIn(name="王媽媽", relation="mother")
    assert g.can_pickup is True
    assert g.receives_notifications is True
    assert g.is_primary is False
    assert g.phone is None


def test_guardian_schemas_create_limits() -> None:
    with pytest.raises(ValidationError):
        GuardianCreateIn(name="", relation="father")
    with pytest.raises(ValidationError):
        GuardianCreateIn(name="王" * 51, relation="father")
    with pytest.raises(ValidationError):
        GuardianCreateIn(name="王爸爸", relation="father", phone="0" * 21)
    assert GuardianCreateIn(name="王爸爸", relation="father", phone="0" * 20).phone == "0" * 20
    with pytest.raises(ValidationError) as exc:
        GuardianCreateIn.model_validate({"name": "王爸爸", "relation": "father", "x": 1})
    assert exc.value.errors()[0]["type"] == "extra_forbidden"


def test_guardian_schemas_update_requires_field() -> None:
    with pytest.raises(ValidationError):
        GuardianUpdateIn.model_validate({})
    with pytest.raises(ValidationError) as exc:
        GuardianUpdateIn.model_validate({"student_id": str(uuid4())})
    assert "extra_forbidden" in {e["type"] for e in exc.value.errors()}
    assert GuardianUpdateIn.model_validate({"phone": None}).phone is None
    with pytest.raises(ValidationError):
        GuardianUpdateIn.model_validate({"name": None})


def test_guardian_schemas_binding_and_out() -> None:
    with pytest.raises(ValidationError):
        GuardianBindingOut(status="pending")
    expires = datetime(2026, 9, 8, tzinfo=UTC)
    binding = GuardianBindingOut(status="code_issued", code_expires_at=expires)
    assert binding.parent_display_name is None
    assert binding.code_expires_at == expires
    out = GuardianOut(
        id=uuid4(),
        student_id=uuid4(),
        name="王媽媽",
        relation="mother",
        phone=None,
        is_primary=True,
        can_pickup=True,
        receives_notifications=True,
        binding=GuardianBindingOut(status="unbound"),
    )
    assert out.binding.status == "unbound"
    code = BindingCodeOut(guardian_id=out.id, code="ABCD1234", expires_at=expires)
    assert code.code == "ABCD1234"
