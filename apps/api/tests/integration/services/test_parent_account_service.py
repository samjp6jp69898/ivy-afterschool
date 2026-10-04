"""BACKEND-063：app/services/parent_account_service.py（get_me）。"""

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.api.deps import CurrentParent
from app.services.parent_account_service import get_me
from tests.support.factories import make_guardian, make_parent, make_student
from tests.support.fake_clock import FakeClock
from tests.support.fake_storage import FakeStorage

_CLOCK = FakeClock(datetime(2026, 9, 10, 4, 0, tzinfo=UTC))


def _current(parent_id, display_name="王媽媽") -> CurrentParent:  # type: ignore[no-untyped-def]
    return CurrentParent(
        id=parent_id, line_user_id="U1", display_name=display_name, token_version=0
    )


def test_parent_get_me_children(db_session: Session) -> None:
    parent = make_parent(db_session, display_name="王媽媽")
    parent.picture_url = "https://profile.line-scdn.net/abc"
    parent.phone = "0912-000-123"
    visible = make_student(db_session, name="王小明")
    hidden = make_student(db_session, name="王小華")
    make_guardian(db_session, visible, parent=parent)
    make_guardian(db_session, hidden, parent=parent, archived=True)
    make_guardian(
        db_session, make_student(db_session, name="他人小孩"), parent=make_parent(db_session)
    )
    db_session.flush()

    me = get_me(db_session, parent=_current(parent.id), storage=FakeStorage(), clock=_CLOCK)

    assert me.id == parent.id
    assert me.display_name == "王媽媽"
    assert me.picture_url == "https://profile.line-scdn.net/abc"
    assert me.phone == "0912-000-123"
    assert [c.name for c in me.children] == ["王小明"]
    assert me.children[0].id == visible.id


def test_parent_get_me_empty(db_session: Session) -> None:
    parent = make_parent(db_session, display_name="王媽媽")

    me = get_me(db_session, parent=_current(parent.id), storage=FakeStorage(), clock=_CLOCK)

    assert me.children == []
    assert me.display_name == "王媽媽"
    assert me.picture_url is None
    assert me.phone is None


def test_parent_get_me_archived_student_hidden(db_session: Session) -> None:
    parent = make_parent(db_session)
    make_guardian(db_session, make_student(db_session, archived=True), parent=parent)

    me = get_me(db_session, parent=_current(parent.id), storage=FakeStorage(), clock=_CLOCK)

    assert me.children == []
