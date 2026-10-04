"""BACKEND-101：app/models/audit.py（AuditLog，append-only）。

以 app_backend 的 db_session 寫入 / 讀回；update 由 DB 權限（DB-006 只授 select / insert）拒絕。
"""

from uuid import UUID

import pytest
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from app.models.audit import AuditLog


def _make_log(db_session: Session) -> AuditLog:
    log = AuditLog(
        actor_type="system",
        action="settings.update",
        entity_type="system_setting",
        entity_id="org.profile",
        after={"name": "快樂安親班"},
        ip="203.0.113.5",
    )
    db_session.add(log)
    db_session.flush()
    return log


def test_audit_model_insert(db_session: Session) -> None:
    log = _make_log(db_session)
    db_session.refresh(log)

    assert isinstance(log.id, UUID)
    assert log.actor_id is None
    assert log.ip == "203.0.113.5"
    assert isinstance(log.ip, str)
    assert log.after is not None
    assert log.after["name"] == "快樂安親班"
    assert log.before is None
    assert log.user_agent is None
    assert log.created_at.tzinfo is not None


def test_audit_model_update_denied(db_session: Session) -> None:
    log = _make_log(db_session)

    log.action = "x.y"
    with pytest.raises(ProgrammingError) as excinfo:
        db_session.flush()
    assert excinfo.value.orig is not None
    assert getattr(excinfo.value.orig, "sqlstate", None) == "42501"
    db_session.rollback()
