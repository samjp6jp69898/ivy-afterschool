"""BACKEND-211：app/notifications/inbox_service.py（站內通知列表與未讀數）。"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.core.pagination import PageParams
from app.models.notifications import Notification
from app.notifications.inbox_service import list_notifications
from app.notifications.recipients import Recipient
from app.schemas.notifications import NotificationListQuery

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
