"""BACKEND-102：app/services/audit_service.py（record、Actor）。

稽核與業務同交易：只 add + flush，不 commit。
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import CurrentParent, CurrentStaff
from app.core.request_meta import RequestMeta
from app.models.audit import AuditLog
from app.services.audit_service import Actor, record
from tests.support.factories import make_staff


def _count(db: Session, action: str, entity_id: str) -> int:
    return len(
        db.execute(
            select(AuditLog.id).where(AuditLog.action == action, AuditLog.entity_id == entity_id)
        ).all()
    )


def test_audit_record_fields(db_session: Session) -> None:
    staff = make_staff(db_session)
    rid = uuid4()

    row = record(
        db_session,
        actor=Actor(type="staff", id=staff.id),
        action="role.update",
        entity_type="role",
        entity_id=rid,
        before={"name": "A"},
        after={"name": "B"},
        meta=RequestMeta(ip="203.0.113.5", user_agent="UA", request_id="r1"),
    )

    stored = db_session.execute(select(AuditLog).where(AuditLog.id == row.id)).scalar_one()
    assert stored.actor_type == "staff"
    assert stored.actor_id == staff.id
    assert stored.action == "role.update"
    assert stored.entity_type == "role"
    assert stored.entity_id == str(rid)
    assert stored.before == {"name": "A"}
    assert stored.after == {"name": "B"}
    assert stored.ip == "203.0.113.5"
    assert stored.user_agent == "UA"


def test_audit_record_same_transaction(db_session: Session) -> None:
    staff = make_staff(db_session)
    rid = uuid4()
    record(
        db_session,
        actor=Actor(type="staff", id=staff.id),
        action="role.update",
        entity_type="role",
        entity_id=rid,
    )
    assert _count(db_session, "role.update", str(rid)) == 1

    db_session.rollback()

    assert _count(db_session, "role.update", str(rid)) == 0


@pytest.mark.parametrize("action", ["RoleUpdate", "role", "role.update.x", "role.Update", ""])
def test_audit_record_invalid_action(db_session: Session, action: str) -> None:
    with pytest.raises(ValueError, match="action"):
        record(
            db_session,
            actor=Actor.system(),
            action=action,
            entity_type="role",
            entity_id=None,
        )


def test_audit_record_actor_without_id_rejected(db_session: Session) -> None:
    with pytest.raises(ValueError, match="actor"):
        record(
            db_session,
            actor=Actor(type="staff", id=None),
            action="role.update",
            entity_type="role",
            entity_id=None,
        )


def test_audit_record_redacts_and_serializes(db_session: Session) -> None:
    row = record(
        db_session,
        actor=Actor.system(),
        action="settings.update",
        entity_type="setting",
        entity_id="line.messaging",
        after={
            "password": "x",
            "nested": {"channel_secret": "y"},
            "items": [{"access_token": "z", "ok": 1}],
            "when": date(2026, 9, 1),
            "at": datetime(2026, 9, 1, 8, 30, tzinfo=UTC),
            "amount": Decimal("1.50"),
            "id": UUID(int=1),
            "student_id_number": "A123456789",
            "health_note": "過敏",
            "code_hash": "abc",
        },
    )

    stored = db_session.execute(select(AuditLog).where(AuditLog.id == row.id)).scalar_one()
    assert stored.after == {
        "password": "***",
        "nested": {"channel_secret": "***"},
        "items": [{"access_token": "***", "ok": 1}],
        "when": "2026-09-01",
        "at": "2026-09-01T08:30:00+00:00",
        "amount": "1.50",
        "id": "00000000-0000-0000-0000-000000000001",
        "student_id_number": "***",
        "health_note": "***",
        "code_hash": "***",
    }


def test_audit_record_system_actor(db_session: Session) -> None:
    row = record(
        db_session,
        actor=Actor.system(),
        action="student.promote_grade",
        entity_type="student",
        entity_id=None,
    )

    stored = db_session.execute(select(AuditLog).where(AuditLog.id == row.id)).scalar_one()
    assert stored.actor_type == "system"
    assert stored.actor_id is None
    assert stored.entity_id is None
    assert stored.before is None
    assert stored.after is None
    assert stored.ip is None


def test_audit_record_actor_constructors() -> None:
    sid, pid = uuid4(), uuid4()
    staff = CurrentStaff(
        id=sid,
        username="u",
        display_name="王小明",
        role_id=uuid4(),
        role_code="admin",
        role_name="管理員",
        permissions=frozenset(),
        must_change_password=False,
        token_version=0,
    )
    parent = CurrentParent(id=pid, line_user_id="U1", display_name=None, token_version=0)

    assert Actor.staff(staff) == Actor(type="staff", id=sid)
    assert Actor.parent(parent) == Actor(type="parent", id=pid)
    assert Actor.system() == Actor(type="system", id=None)
