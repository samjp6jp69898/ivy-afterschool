"""BACKEND-204：app/notifications/recipients.py（家長通知收件人解析）。"""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import event
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from app.core.permissions import Permission
from app.notifications.recipients import (
    Recipient,
    parent_recipients,
    parent_recipients_bulk,
    staff_recipients,
)
from tests.support.factories import (
    make_class,
    make_class_staff,
    make_guardian,
    make_parent,
    make_staff,
    make_student,
)


@contextmanager
def _count_statements(session: Session) -> Iterator[list[str]]:
    bind = session.get_bind()
    assert isinstance(bind, Connection)
    statements: list[str] = []

    def _record(conn: Connection, cursor: object, statement: str, *args: object) -> None:
        statements.append(statement)

    event.listen(bind, "before_cursor_execute", _record)
    try:
        yield statements
    finally:
        event.remove(bind, "before_cursor_execute", _record)


def test_parent_recipients_filters(db_session: Session) -> None:
    s = make_student(db_session, name="王小明")
    p1, p2, p3 = (make_parent(db_session) for _ in range(3))
    p4 = make_parent(db_session, status="disabled")
    make_guardian(db_session, s, parent=p1, name="媽媽")
    make_guardian(db_session, s, parent=p2, name="爸爸", receives_notifications=False)
    make_guardian(db_session, s, parent=None, name="奶奶")
    make_guardian(db_session, s, parent=p3, name="阿姨", archived=True)
    make_guardian(db_session, s, parent=p4, name="叔叔")
    sid = s.id

    assert parent_recipients(db_session, sid) == [Recipient("parent", p1.id)]


def test_parent_recipients_dedup_and_sorted(db_session: Session) -> None:
    s = make_student(db_session)
    parents = [make_parent(db_session) for _ in range(3)]
    for p in parents:
        make_guardian(db_session, s, parent=p)
    # 同一家長另有一筆封存 guardian：仍只出現一次
    make_guardian(db_session, s, parent=parents[0], archived=True)

    result = parent_recipients(db_session, s.id)

    assert result == [Recipient("parent", i) for i in sorted(p.id for p in parents)]


def test_parent_recipients_bulk(db_session: Session) -> None:
    ming = make_student(db_session, name="王小明")
    hua = make_student(db_session, name="陳小華")
    an = make_student(db_session, name="林小安")
    p1 = make_parent(db_session)
    make_guardian(db_session, ming, parent=p1)
    make_guardian(db_session, hua, parent=p1)
    make_guardian(db_session, an, parent=None)
    ids = [ming.id, hua.id, an.id]

    with _count_statements(db_session) as statements:
        result = parent_recipients_bulk(db_session, ids)

    assert result == {
        ming.id: [Recipient("parent", p1.id)],
        hua.id: [Recipient("parent", p1.id)],
        an.id: [],
    }
    assert len(statements) == 1


def test_parent_recipients_bulk_empty_input(db_session: Session) -> None:
    with _count_statements(db_session) as statements:
        assert parent_recipients_bulk(db_session, []) == {}
    assert statements == []


def test_parent_recipients_archived_student(db_session: Session) -> None:
    s = make_student(db_session, archived=True)
    make_guardian(db_session, s, parent=make_parent(db_session))
    live = make_student(db_session)

    assert parent_recipients(db_session, s.id) == []
    assert parent_recipients_bulk(db_session, [s.id, live.id]) == {s.id: [], live.id: []}


def _ids(recipients: list[Recipient]) -> set[object]:
    assert all(r.type == "staff" for r in recipients)
    return {r.id for r in recipients}


def test_staff_recipients_permission(db_session: Session) -> None:
    pickup = ["pickup:operate"]
    a = make_staff(db_session, permissions=pickup)
    b = make_staff(db_session, permissions=[], extra_permissions=pickup)
    c = make_staff(db_session, permissions=pickup, revoked_permissions=pickup)
    d = make_staff(db_session, role_code="admin")
    e = make_staff(db_session, permissions=pickup, is_active=False)
    no_perm = make_staff(db_session, permissions=["students:read"])

    ids = _ids(staff_recipients(db_session, permission=Permission.PICKUP_OPERATE))

    assert {a.id, b.id, d.id} <= ids
    assert not {c.id, e.id, no_perm.id} & ids


def test_staff_recipients_admin_wildcard_and_revoked_admin(db_session: Session) -> None:
    admin = make_staff(db_session, role_code="admin")
    revoked_admin = make_staff(db_session, role_code="admin", revoked_permissions=["leaves:read"])

    ids = _ids(staff_recipients(db_session, permission=Permission.LEAVES_READ))

    assert admin.id in ids
    assert revoked_admin.id not in ids


def test_staff_recipients_class_staff(db_session: Session) -> None:
    class_a = make_class(db_session)
    class_b = make_class(db_session)
    f = make_staff(db_session, permissions=[])
    other_class = make_staff(db_session, permissions=[])
    inactive = make_staff(db_session, permissions=[], is_active=False)
    make_class_staff(db_session, class_a, f, role="assistant")
    make_class_staff(db_session, class_b, other_class, role="lead")
    make_class_staff(db_session, class_a, inactive, role="lead")

    ids = _ids(
        staff_recipients(db_session, permission=Permission.LEAVES_READ, class_ids=[class_a.id])
    )

    assert f.id in ids
    assert other_class.id not in ids
    assert inactive.id not in ids
    # 沒給 permission 時只看班級
    only_class = _ids(staff_recipients(db_session, class_ids=[class_a.id]))
    assert only_class == {f.id}


def test_staff_recipients_dedupe_and_empty(db_session: Session) -> None:
    klass = make_class(db_session)
    a = make_staff(db_session, permissions=["leaves:read"])
    make_class_staff(db_session, klass, a, role="lead")

    result = staff_recipients(
        db_session, permission=Permission.LEAVES_READ, class_ids=[klass.id, klass.id]
    )

    assert [r.id for r in result].count(a.id) == 1
    assert [r.id for r in result] == sorted(r.id for r in result)
    assert staff_recipients(db_session) == []
    assert staff_recipients(db_session, class_ids=[]) == []
