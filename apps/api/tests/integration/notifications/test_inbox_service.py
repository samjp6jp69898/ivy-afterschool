"""BACKEND-211：app/notifications/inbox_service.py（站內通知列表與未讀數）。"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.pagination import PageParams
from app.models.notifications import Notification
from app.notifications.inbox_service import list_notifications, mark_all_read, mark_read
from app.notifications.recipients import Recipient
from app.schemas.notifications import MarkAllReadOut, NotificationListQuery
from tests.support.fake_clock import FakeClock

_PAGE = PageParams(page=1, page_size=50)
_READ_AT = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


def _note(
    db: Session,
    recipient_type: str,
    recipient_id: UUID,
    *,
    title: str = "到班通知",
    event: str = "attendance.checked_in",
    read: bool = False,
    hour: int = 8,
) -> Notification:
    row = Notification(
        recipient_type=recipient_type,
        recipient_id=recipient_id,
        event=event,
        title=title,
        body="王小明 已到班",
        payload={"student_id": str(uuid4())},
        read_at=_READ_AT if read else None,
        created_at=datetime(2026, 9, 1, hour, tzinfo=UTC),
    )
    db.add(row)
    db.flush()
    return row


def _seed(db: Session) -> tuple[UUID, UUID]:
    p1, p2 = uuid4(), uuid4()
    _note(db, "parent", p1, title="p1-a", hour=8)
    _note(db, "parent", p1, title="p1-b", hour=9, read=True)
    _note(db, "parent", p1, title="p1-c", hour=10)
    _note(db, "parent", p2, title="p2-a")
    _note(db, "parent", p2, title="p2-b")
    _note(db, "staff", p1, title="staff-with-p1-id")
    return p1, p2


def test_inbox_list_own_only(db_session: Session) -> None:
    p1, _ = _seed(db_session)

    page = list_notifications(db_session, Recipient("parent", p1), NotificationListQuery(), _PAGE)

    assert page.total == 3
    assert page.unread_count == 2
    assert {i.title for i in page.items} == {"p1-a", "p1-b", "p1-c"}
    staff_page = list_notifications(
        db_session, Recipient("staff", p1), NotificationListQuery(), _PAGE
    )
    assert [i.title for i in staff_page.items] == ["staff-with-p1-id"]


def test_inbox_list_unread_only(db_session: Session) -> None:
    p1, _ = _seed(db_session)

    page = list_notifications(
        db_session, Recipient("parent", p1), NotificationListQuery(unread_only=True), _PAGE
    )

    assert page.total == 2
    assert {i.title for i in page.items} == {"p1-a", "p1-c"}
    assert all(i.read_at is None for i in page.items)
    assert page.unread_count == 2


def test_inbox_list_order(db_session: Session) -> None:
    p1, _ = _seed(db_session)

    page = list_notifications(db_session, Recipient("parent", p1), NotificationListQuery(), _PAGE)

    assert [i.title for i in page.items] == ["p1-c", "p1-b", "p1-a"]


def test_inbox_list_unread_count_ignores_paging(db_session: Session) -> None:
    p1, _ = _seed(db_session)

    page = list_notifications(
        db_session,
        Recipient("parent", p1),
        NotificationListQuery(),
        PageParams(page=2, page_size=2),
    )

    assert page.total == 3
    assert [i.title for i in page.items] == ["p1-a"]
    assert page.unread_count == 2


def test_inbox_list_item_fields(db_session: Session) -> None:
    owner = uuid4()
    row = _note(db_session, "parent", owner, event="exam.published", read=True)

    (item,) = list_notifications(
        db_session, Recipient("parent", owner), NotificationListQuery(), _PAGE
    ).items

    assert item.id == row.id
    assert item.event == "exam.published"
    assert item.deep_link == "/exams"
    assert item.read_at == _READ_AT
    assert item.body == "王小明 已到班"
    assert item.payload == row.payload


def test_inbox_list_empty(db_session: Session) -> None:
    page = list_notifications(
        db_session, Recipient("parent", uuid4()), NotificationListQuery(), _PAGE
    )

    assert (page.items, page.total, page.unread_count) == ([], 0, 0)


def test_inbox_mark_read(db_session: Session, fake_clock: FakeClock) -> None:
    p1 = uuid4()
    note = _note(db_session, "parent", p1)
    assert note.read_at is None

    out = mark_read(db_session, Recipient("parent", p1), note.id, clock=fake_clock)

    assert out.id == note.id
    assert out.read_at == fake_clock.now()
    assert out.deep_link == "/attendance"
    first = out.read_at
    stored = db_session.execute(select(Notification).where(Notification.id == note.id)).scalar_one()
    assert stored.read_at == first

    fake_clock.advance(minutes=5)
    again = mark_read(db_session, Recipient("parent", p1), note.id, clock=fake_clock)

    assert again.read_at == first  # 冪等：保留原 read_at


def test_inbox_mark_read_keeps_existing_read_at(db_session: Session, fake_clock: FakeClock) -> None:
    p1 = uuid4()
    note = _note(db_session, "parent", p1, read=True)

    out = mark_read(db_session, Recipient("parent", p1), note.id, clock=fake_clock)

    assert out.read_at == _READ_AT


def test_inbox_mark_read_idor(db_session: Session, fake_clock: FakeClock) -> None:
    p1, p2 = uuid4(), uuid4()
    note = _note(db_session, "parent", p1)
    same_id_staff = _note(db_session, "staff", p1)

    with pytest.raises(AppError) as other_parent:
        mark_read(db_session, Recipient("parent", p2), note.id, clock=fake_clock)
    with pytest.raises(AppError) as other_type:
        mark_read(db_session, Recipient("staff", p1), note.id, clock=fake_clock)
    with pytest.raises(AppError) as missing:
        mark_read(db_session, Recipient("parent", p1), uuid4(), clock=fake_clock)

    for exc in (other_parent, other_type, missing):
        assert (exc.value.status, exc.value.code) == (404, "notification_not_found")
    assert {
        (e.value.status, e.value.code, e.value.message) for e in (other_parent, other_type, missing)
    } == {(404, "notification_not_found", "找不到通知")}
    # 別人的通知沒被動到
    db_session.refresh(note)
    db_session.refresh(same_id_staff)
    assert note.read_at is None
    assert same_id_staff.read_at is None


def test_inbox_mark_all_read(db_session: Session, fake_clock: FakeClock) -> None:
    s, other = uuid4(), uuid4()
    for _ in range(3):
        _note(db_session, "staff", s)
    already = _note(db_session, "staff", s, read=True)
    for _ in range(2):
        _note(db_session, "staff", other)
    _note(db_session, "parent", s)  # 同 id 不同類型的收件人

    result = mark_all_read(db_session, Recipient("staff", s), clock=fake_clock)

    assert result == MarkAllReadOut(updated=3)
    db_session.refresh(already)
    assert already.read_at == _READ_AT
    mine = list_notifications(
        db_session, Recipient("staff", s), NotificationListQuery(unread_only=True), _PAGE
    )
    assert mine.total == 0
    others = list_notifications(
        db_session, Recipient("staff", other), NotificationListQuery(unread_only=True), _PAGE
    )
    assert others.total == 2
    parent_side = list_notifications(
        db_session, Recipient("parent", s), NotificationListQuery(unread_only=True), _PAGE
    )
    assert parent_side.total == 1
    assert {
        i.read_at
        for i in list_notifications(
            db_session, Recipient("staff", s), NotificationListQuery(), _PAGE
        ).items
    } == {fake_clock.now(), _READ_AT}


def test_inbox_mark_all_read_none(db_session: Session, fake_clock: FakeClock) -> None:
    owner = uuid4()
    _note(db_session, "staff", owner, read=True)

    assert mark_all_read(db_session, Recipient("staff", owner), clock=fake_clock).updated == 0
    assert mark_all_read(db_session, Recipient("staff", uuid4()), clock=fake_clock).updated == 0
