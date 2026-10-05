"""BACKEND-347 / 350 / 348 / 344：leave_service。

後台請假列表、附件短效 URL、家長端小孩請假列表、請假建立 / 取消通知。
"""

from collections.abc import Iterator
from datetime import UTC, date, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.pagination import PageParams
from app.core.storage import StorageError
from app.models.notifications import Notification
from app.notifications import outbox_jobs
from app.notifications.events import Event
from app.schemas.leaves import AttachmentUrlOut, LeaveListQuery, LeaveOut
from app.services import leave_service
from app.services.leave_service import (
    get_attachment_url,
    list_child_leaves,
    list_leaves,
    notify_leave_event,
)
from tests.support.factories import (
    make_class,
    make_class_staff,
    make_leave,
    make_leave_attachment,
    make_parent,
    make_staff,
    make_student,
)
from tests.support.fake_clock import FakeClock
from tests.support.fake_storage import FakeStorage

_PAGE = PageParams(page=1, page_size=200)


def _mine(rows: list[LeaveOut], ids: set[UUID]) -> list[LeaveOut]:
    """只看本測試建立的請假，避免 DB 內其他資料干擾。"""
    return [row for row in rows if row.id in ids]


def _list(db: Session, ids: set[UUID], **filters: object) -> list[LeaveOut]:
    page = list_leaves(db, LeaveListQuery(**filters), _PAGE)
    return _mine(page.items, ids)


def test_list_leaves_filters(db_session: Session) -> None:
    class_a = make_class(db_session, name="A班")
    class_b = make_class(db_session, name="B班")
    ming = make_student(db_session, name="王小明", class_=class_a)
    hua = make_student(db_session, name="陳小華", class_=class_b)
    parent = make_parent(db_session)
    teacher = make_staff(db_session)
    sick = make_leave(
        db_session,
        ming,
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 2),
        created_by_type="parent",
        created_by_id=parent.id,
    )
    personal = make_leave(
        db_session,
        hua,
        start_date=date(2026, 9, 5),
        leave_type="personal",
        created_by_id=teacher.id,
    )
    cancelled = make_leave(db_session, ming, start_date=date(2026, 9, 10), status="cancelled")
    ids = {sick.id, personal.id, cancelled.id}

    def got(**filters: object) -> set[UUID]:
        return {row.id for row in _list(db_session, ids, **filters)}

    assert got() == ids
    assert got(status="active") == {sick.id, personal.id}
    assert got(status="cancelled") == {cancelled.id}
    # 日期區間交集含頭尾：9/2 是 sick 的最後一天、9/5 是 personal 的當天
    assert got(date_from=date(2026, 9, 2), date_to=date(2026, 9, 5)) == {sick.id, personal.id}
    assert got(date_from=date(2026, 9, 3), date_to=date(2026, 9, 4)) == set()
    assert got(date_from=date(2026, 9, 6)) == {cancelled.id}
    assert got(date_to=date(2026, 8, 31)) == set()
    assert got(leave_type="personal") == {personal.id}
    assert got(class_id=class_b.id) == {personal.id}
    assert got(student_id=ming.id) == {sick.id, cancelled.id}
    assert got(created_by_type="parent") == {sick.id}
    assert got(created_by_type="staff") == {personal.id, cancelled.id}


def test_list_leaves_creator_names(db_session: Session) -> None:
    ming = make_student(db_session, name="王小明")
    hua = make_student(db_session, name="陳小華")
    parent = make_parent(db_session, display_name="王媽媽")
    teacher = make_staff(db_session, display_name="林老師")
    by_parent = make_leave(
        db_session,
        ming,
        start_date=date(2026, 9, 1),
        created_by_type="parent",
        created_by_id=parent.id,
    )
    by_staff = make_leave(db_session, hua, start_date=date(2026, 9, 2), created_by_id=teacher.id)
    cancelled = make_leave(
        db_session,
        ming,
        start_date=date(2026, 9, 10),
        status="cancelled",
        created_by_type="parent",
        created_by_id=parent.id,
    )
    cancelled.cancelled_by_type = "staff"
    cancelled.cancelled_by_id = teacher.id
    attachment = make_leave_attachment(db_session, by_parent)
    db_session.flush()

    rows = {r.id: r for r in _list(db_session, {by_parent.id, by_staff.id, cancelled.id})}

    assert (rows[by_parent.id].created_by_type, rows[by_parent.id].created_by_name) == (
        "parent",
        "王媽媽",
    )
    assert (rows[by_staff.id].created_by_type, rows[by_staff.id].created_by_name) == (
        "staff",
        "林老師",
    )
    assert rows[by_parent.id].cancelled_by_name is None
    assert (rows[cancelled.id].cancelled_by_type, rows[cancelled.id].cancelled_by_name) == (
        "staff",
        "林老師",
    )
    assert rows[cancelled.id].created_by_name == "王媽媽"
    student = rows[by_parent.id].student
    assert (student.id, student.student_no, student.name, student.class_name) == (
        ming.id,
        ming.student_no,
        "王小明",
        None,
    )
    assert rows[by_parent.id].leave_type_label == "病假"
    assert [(a.id, a.mime_type, a.size_bytes) for a in rows[by_parent.id].attachments] == [
        (attachment.id, attachment.mime_type, attachment.size_bytes)
    ]
    assert rows[by_staff.id].attachments == []


def test_list_leaves_order_and_query_count(db_session: Session) -> None:
    parent = make_parent(db_session)
    teacher = make_staff(db_session)
    ids: set[UUID] = set()
    for day in range(1, 21):
        student = make_student(db_session)
        leave = make_leave(
            db_session,
            student,
            start_date=date(2026, 9, day),
            created_by_type="parent" if day % 2 else "staff",
            created_by_id=parent.id if day % 2 else teacher.id,
        )
        make_leave_attachment(db_session, leave)
        ids.add(leave.id)
    db_session.expire_all()

    statements: list[str] = []

    def record(_conn: object, _cur: object, statement: str, *_args: object) -> None:
        statements.append(statement)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", record)
    try:
        page = list_leaves(db_session, LeaveListQuery(), _PAGE)
    finally:
        event.remove(engine, "before_cursor_execute", record)

    mine = _mine(page.items, ids)
    assert len(mine) == 20
    assert [r.start_date for r in mine] == sorted((r.start_date for r in mine), reverse=True)
    assert mine[0].start_date == date(2026, 9, 20)
    assert page.total >= 20
    assert len(statements) <= 5


def test_list_leaves_same_start_newest_created_first(db_session: Session) -> None:
    ming = make_student(db_session)
    hua = make_student(db_session, name="陳小華")
    older = make_leave(db_session, ming, start_date=date(2026, 9, 1))
    newer = make_leave(db_session, hua, start_date=date(2026, 9, 1))
    older.created_at = datetime(2026, 8, 8, tzinfo=UTC)
    newer.created_at = datetime(2026, 8, 9, tzinfo=UTC)
    db_session.flush()

    rows = _list(db_session, {older.id, newer.id})

    assert [r.id for r in rows] == [newer.id, older.id]


def test_list_leaves_pagination(db_session: Session) -> None:
    student = make_student(db_session)
    leaves = [
        make_leave(db_session, student, start_date=date(2026, 9, 1 + 5 * i)) for i in range(3)
    ]
    only = LeaveListQuery(student_id=student.id)

    second = list_leaves(db_session, only, PageParams(page=2, page_size=2))

    assert second.total == 3
    assert [r.id for r in second.items] == [leaves[0].id]


def test_attachment_url_success(db_session: Session) -> None:
    storage = FakeStorage()
    leave = make_leave(db_session, make_student(db_session), start_date=date(2026, 9, 1))
    attachment = make_leave_attachment(db_session, leave)

    out = get_attachment_url(db_session, leave.id, attachment.id, storage=storage)

    assert out == AttachmentUrlOut(
        url=f"https://storage.test/leave-attachments/{attachment.storage_path}?exp=300",
        expires_in=300,
    )


def test_attachment_url_mismatch(db_session: Session) -> None:
    storage = FakeStorage()
    ming = make_student(db_session)
    leave = make_leave(db_session, ming, start_date=date(2026, 9, 1))
    other_leave = make_leave(db_session, ming, start_date=date(2026, 9, 10))
    attachment = make_leave_attachment(db_session, leave)

    with pytest.raises(AppError) as wrong_leave:
        get_attachment_url(db_session, other_leave.id, attachment.id, storage=storage)
    with pytest.raises(AppError) as missing:
        get_attachment_url(db_session, leave.id, uuid4(), storage=storage)
    storage.sign_error = StorageError("S3 generate_presigned_url 失敗")
    with pytest.raises(AppError) as unavailable:
        get_attachment_url(db_session, leave.id, attachment.id, storage=storage)

    for exc in (wrong_leave, missing):
        assert (exc.value.status, exc.value.code) == (404, "attachment_not_found")
    assert wrong_leave.value.message == missing.value.message
    assert (unavailable.value.status, unavailable.value.code) == (502, "storage_unavailable")


# --- BACKEND-348 list_child_leaves ---


@pytest.mark.clock("2026-09-02T10:00:00+08:00")
def test_list_child_leaves(db_session: Session, fake_clock: FakeClock) -> None:
    ming = make_student(db_session, name="王小明")
    hua = make_student(db_session, name="陳小華")
    # 同一學生的 active 請假不可重疊（ex_student_leaves_no_overlap）：已結束的那筆放在 8/28
    ended = make_leave(db_session, ming, start_date=date(2026, 8, 28))
    ongoing = make_leave(db_session, ming, start_date=date(2026, 8, 31), end_date=date(2026, 9, 3))
    future = make_leave(db_session, ming, start_date=date(2026, 9, 10), leave_type="personal")
    make_leave(db_session, hua, start_date=date(2026, 9, 2))

    page = list_child_leaves(db_session, ming.id, _PAGE, storage=FakeStorage(), clock=fake_clock)

    assert page.total == 3
    assert [r.id for r in page.items] == [future.id, ongoing.id, ended.id]
    assert {r.id: r.can_cancel for r in page.items} == {
        future.id: True,
        ended.id: False,
        ongoing.id: True,
    }
    assert {r.student_id for r in page.items} == {ming.id}
    first = page.items[0]
    assert (first.leave_type, first.leave_type_label, first.status) == (
        "personal",
        "事假",
        "active",
    )
    # 家長端不回傳員工姓名
    assert "created_by_name" not in first.model_dump()


@pytest.mark.clock("2026-09-02T10:00:00+08:00")
def test_list_child_leaves_includes_cancelled_and_last_day(
    db_session: Session, fake_clock: FakeClock
) -> None:
    ming = make_student(db_session)
    ends_today = make_leave(
        db_session, ming, start_date=date(2026, 9, 1), end_date=date(2026, 9, 2)
    )
    cancelled = make_leave(db_session, ming, start_date=date(2026, 9, 5), status="cancelled")

    page = list_child_leaves(db_session, ming.id, _PAGE, storage=FakeStorage(), clock=fake_clock)

    rows = {r.id: r for r in page.items}
    assert page.total == 2
    # 今天是最後一天仍可取消（取消今天）
    assert rows[ends_today.id].can_cancel is True
    assert (rows[cancelled.id].status, rows[cancelled.id].can_cancel) == ("cancelled", False)
    assert rows[cancelled.id].cancelled_at is not None


def test_list_child_leaves_same_start_newest_created_first(
    db_session: Session, fake_clock: FakeClock
) -> None:
    ming = make_student(db_session)
    older = make_leave(db_session, ming, start_date=date(2026, 9, 1), status="cancelled")
    newer = make_leave(db_session, ming, start_date=date(2026, 9, 1))
    older.created_at = datetime(2026, 8, 8, tzinfo=UTC)
    newer.created_at = datetime(2026, 8, 9, tzinfo=UTC)
    db_session.flush()

    page = list_child_leaves(db_session, ming.id, _PAGE, storage=FakeStorage(), clock=fake_clock)

    assert [r.id for r in page.items] == [newer.id, older.id]


def test_list_child_leaves_attachment_url(db_session: Session, fake_clock: FakeClock) -> None:
    storage = FakeStorage()
    ming = make_student(db_session)
    leave = make_leave(db_session, ming, start_date=date(2026, 9, 1))
    attachment = make_leave_attachment(db_session, leave, ext="jpg")

    page = list_child_leaves(db_session, ming.id, _PAGE, storage=storage, clock=fake_clock)

    [row] = page.items
    [out] = row.attachments
    assert out.url == f"https://storage.test/leave-attachments/{attachment.storage_path}?exp=300"
    assert (out.id, out.mime_type, out.size_bytes) == (attachment.id, "image/jpeg", 1024)

    storage.sign_error = StorageError("S3 generate_presigned_url 失敗")
    failed = list_child_leaves(db_session, ming.id, _PAGE, storage=storage, clock=fake_clock)

    [failed_row] = failed.items
    assert failed_row.id == leave.id
    assert [a.url for a in failed_row.attachments] == [None]
    assert failed_row.attachments[0].id == attachment.id


def test_list_child_leaves_pagination(db_session: Session, fake_clock: FakeClock) -> None:
    ming = make_student(db_session)
    leaves = [make_leave(db_session, ming, start_date=date(2026, 9, 1 + 5 * i)) for i in range(3)]

    second = list_child_leaves(
        db_session,
        ming.id,
        PageParams(page=2, page_size=2),
        storage=FakeStorage(),
        clock=fake_clock,
    )

    assert second.total == 3
    assert [r.id for r in second.items] == [leaves[0].id]


# --- BACKEND-344 notify_leave_event ---


@pytest.fixture
def kick_off() -> Iterator[None]:
    """commit 後的 outbox kick 不實際派送（員工事件沒有 line，保險起見仍關閉）。"""
    outbox_jobs.set_kick_mode("off")
    yield
    outbox_jobs.set_kick_mode("thread")


def _leave_notifications(db: Session, event_name: str, leave_id: UUID) -> list[Notification]:
    return list(
        db.execute(
            select(Notification).where(
                Notification.event == event_name,
                Notification.payload["leave_id"].astext == str(leave_id),
            )
        ).scalars()
    )


def test_notify_leave_created_recipients(
    db_session: Session, fake_clock: FakeClock, kick_off: None
) -> None:
    class_a = make_class(db_session, name="A班")
    class_b = make_class(db_session, name="B班")
    ming = make_student(db_session, name="王小明", class_=class_a)
    f = make_staff(db_session, permissions=[], display_name="助教 f")
    g = make_staff(db_session, permissions=["leaves:read"], display_name="員工 g")
    h = make_staff(db_session, permissions=[], display_name="員工 h")
    other_class = make_staff(db_session, permissions=[], display_name="B班老師")
    inactive = make_staff(db_session, permissions=["leaves:read"], is_active=False)
    make_class_staff(db_session, class_a, f, role="assistant")
    make_class_staff(db_session, class_b, other_class)
    leave = make_leave(db_session, ming, start_date=date(2026, 9, 1), end_date=date(2026, 9, 2))

    notify_leave_event(db_session, leave, Event.LEAVE_CREATED, clock=fake_clock)

    rows = _leave_notifications(db_session, "leave.created", leave.id)
    recipients = {row.recipient_id for row in rows}
    assert {f.id, g.id} <= recipients
    assert not {h.id, other_class.id, inactive.id} & recipients
    assert {row.recipient_type for row in rows} == {"staff"}
    assert rows[0].payload == {
        "student_id": str(ming.id),
        "student_name": "王小明",
        "leave_id": str(leave.id),
        "start_date": "2026-09-01",
        "end_date": "2026-09-02",
        "leave_type_label": "病假",
    }
    assert rows[0].title == "王小明 請假"


def test_notify_leave_without_class(
    db_session: Session, fake_clock: FakeClock, kick_off: None
) -> None:
    ming = make_student(db_session, name="王小明")
    g = make_staff(db_session, permissions=["leaves:read"])
    leave = make_leave(db_session, ming, start_date=date(2026, 9, 1))

    notify_leave_event(db_session, leave, Event.LEAVE_CREATED, clock=fake_clock)

    assert g.id in {
        r.recipient_id for r in _leave_notifications(db_session, "leave.created", leave.id)
    }


def test_notify_leave_cancelled_title(
    db_session: Session, fake_clock: FakeClock, kick_off: None
) -> None:
    ming = make_student(db_session, name="王小明")
    make_staff(db_session, permissions=["leaves:read"])
    leave = make_leave(db_session, ming, start_date=date(2026, 9, 1), status="cancelled")

    notify_leave_event(db_session, leave, Event.LEAVE_CANCELLED, clock=fake_clock)

    rows = _leave_notifications(db_session, "leave.cancelled", leave.id)
    assert rows
    assert {row.title for row in rows} == {"王小明 取消請假"}


def test_notify_leave_cancelled_partial_range(
    db_session: Session, fake_clock: FakeClock, kick_off: None
) -> None:
    ming = make_student(db_session, name="王小明")
    make_staff(db_session, permissions=["leaves:read"])
    leave = make_leave(db_session, ming, start_date=date(2026, 9, 7), end_date=date(2026, 9, 11))

    notify_leave_event(
        db_session,
        leave,
        Event.LEAVE_CANCELLED,
        date_range=(date(2026, 9, 9), date(2026, 9, 11)),
        clock=fake_clock,
    )

    [row, *_] = _leave_notifications(db_session, "leave.cancelled", leave.id)
    assert (row.payload["start_date"], row.payload["end_date"]) == ("2026-09-09", "2026-09-11")


def test_notify_leave_no_recipients(
    db_session: Session, fake_clock: FakeClock, kick_off: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """收件人為空 → 不呼叫 enqueue。"""
    calls: list[object] = []
    monkeypatch.setattr(leave_service, "staff_recipients", lambda *a, **k: [])
    monkeypatch.setattr(leave_service, "enqueue", lambda *a, **k: calls.append(a))
    leave = make_leave(db_session, make_student(db_session), start_date=date(2026, 9, 1))

    notify_leave_event(db_session, leave, Event.LEAVE_CREATED, clock=fake_clock)

    assert calls == []
