"""BACKEND-204：app/notifications/recipients.py（家長通知收件人解析）。"""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import event
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from app.notifications.recipients import Recipient, parent_recipients, parent_recipients_bulk
from tests.support.factories import make_guardian, make_parent, make_student


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
