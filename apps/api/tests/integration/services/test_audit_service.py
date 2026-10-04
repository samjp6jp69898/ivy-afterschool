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
from app.core.pagination import PageParams
from app.core.request_meta import RequestMeta
from app.models.audit import AuditLog
from app.schemas.audit import AuditLogQuery
from app.services.audit_service import Actor, list_audit_logs, record
from tests.support.factories import make_parent, make_staff


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


_PAGE = PageParams(page=1, page_size=50)


def _log(
    db: Session,
    *,
    action: str,
    scope: str,
    actor: Actor | None = None,
    entity_id: str | None = None,
    created_at: datetime | None = None,
) -> AuditLog:
    """scope 為本測試專屬的 entity_type，避免與 DB 內既有資料互相干擾。"""
    # audit_logs append-only（app_backend 無 update 權限），指定 created_at 只能在 insert 時給
    who = actor or Actor.system()
    row = AuditLog(
        actor_type=who.type,
        actor_id=who.id,
        action=action,
        entity_type=scope,
        entity_id=entity_id,
    )
    if created_at is not None:
        row.created_at = created_at
    db.add(row)
    db.flush()
    return row


def test_list_audit_logs_filters(db_session: Session) -> None:
    staff = make_staff(db_session)
    scope = f"scope_{uuid4().hex[:8]}"
    first = _log(db_session, action="role.update", scope=scope, entity_id="r1")
    _log(db_session, action="role.create", scope=scope, entity_id="r2")
    _log(db_session, action="settings.update", scope=scope, actor=Actor("staff", staff.id))

    def run(**kw: object) -> list[str]:
        page = list_audit_logs(db_session, AuditLogQuery(entity_type=scope, **kw), _PAGE)
        return [i.action for i in page.items]

    assert run(action="settings.update") == ["settings.update"]
    assert sorted(run(action_prefix="role.")) == ["role.create", "role.update"]
    assert run(entity_id="r1") == ["role.update"]
    assert run(actor_type="staff") == ["settings.update"]
    assert run(actor_id=staff.id) == ["settings.update"]
    assert run(actor_type="parent") == []
    page = list_audit_logs(
        db_session, AuditLogQuery(entity_type=scope, action_prefix="role."), _PAGE
    )
    assert page.total == 2
    assert first.id in {i.id for i in page.items}


def test_list_audit_logs_prefix_is_literal(db_session: Session) -> None:
    scope = f"scope_{uuid4().hex[:8]}"
    _log(db_session, action="staff_user.create", scope=scope)
    _log(db_session, action="staffxuser.create", scope=scope)

    page = list_audit_logs(
        db_session, AuditLogQuery(entity_type=scope, action_prefix="staff_user."), _PAGE
    )

    assert [i.action for i in page.items] == ["staff_user.create"]


def test_list_audit_logs_taipei_date_range(db_session: Session) -> None:
    scope = f"scope_{uuid4().hex[:8]}"
    _log(
        db_session,
        action="role.update",
        scope=scope,
        entity_id="before_midnight",
        created_at=datetime(2026, 9, 1, 15, 59, tzinfo=UTC),
    )
    _log(
        db_session,
        action="role.update",
        scope=scope,
        entity_id="after_midnight",
        created_at=datetime(2026, 9, 1, 16, 1, tzinfo=UTC),
    )
    _log(
        db_session,
        action="role.update",
        scope=scope,
        entity_id="next_day_edge",
        created_at=datetime(2026, 9, 2, 16, 0, tzinfo=UTC),
    )

    def ids(**kw: date) -> list[str]:
        page = list_audit_logs(db_session, AuditLogQuery(entity_type=scope, **kw), _PAGE)
        return sorted(i.entity_id or "" for i in page.items)

    only = date(2026, 9, 2)
    assert ids(date_from=only, date_to=only) == ["after_midnight"]
    assert ids(date_from=only) == ["after_midnight", "next_day_edge"]
    assert ids(date_to=only) == ["after_midnight", "before_midnight"]
    assert ids(date_from=date(2026, 9, 1), date_to=date(2026, 9, 2)) == [
        "after_midnight",
        "before_midnight",
    ]


def test_list_audit_logs_actor_name(db_session: Session) -> None:
    scope = f"scope_{uuid4().hex[:8]}"
    staff = make_staff(db_session, display_name="林老師")
    parent = make_parent(db_session, display_name="王媽媽")
    _log(db_session, action="a.staff", scope=scope, actor=Actor("staff", staff.id))
    _log(db_session, action="a.parent", scope=scope, actor=Actor("parent", parent.id))
    _log(db_session, action="a.system", scope=scope)
    _log(db_session, action="a.gone", scope=scope, actor=Actor("staff", uuid4()))

    page = list_audit_logs(db_session, AuditLogQuery(entity_type=scope), _PAGE)

    names = {i.action: i.actor_name for i in page.items}
    assert names == {
        "a.staff": "林老師",
        "a.parent": "王媽媽",
        "a.system": "系統",
        "a.gone": None,
    }
    assert {i.action: i.actor_type for i in page.items}["a.parent"] == "parent"


def test_list_audit_logs_order(db_session: Session) -> None:
    scope = f"scope_{uuid4().hex[:8]}"
    for hour, tag in ((9, "mid"), (8, "old"), (10, "new")):
        _log(
            db_session,
            action="a.x",
            scope=scope,
            entity_id=tag,
            created_at=datetime(2026, 9, 1, hour, tzinfo=UTC),
        )

    page = list_audit_logs(db_session, AuditLogQuery(entity_type=scope), _PAGE)

    assert [i.entity_id for i in page.items] == ["new", "mid", "old"]


def test_list_audit_logs_pagination(db_session: Session) -> None:
    scope = f"scope_{uuid4().hex[:8]}"
    for hour in range(5):
        _log(
            db_session,
            action="a.x",
            scope=scope,
            entity_id=str(hour),
            created_at=datetime(2026, 9, 1, hour, tzinfo=UTC),
        )

    page2 = list_audit_logs(
        db_session, AuditLogQuery(entity_type=scope), PageParams(page=2, page_size=2)
    )

    assert page2.total == 5
    assert [i.entity_id for i in page2.items] == ["2", "1"]
