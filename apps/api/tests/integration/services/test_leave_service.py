"""BACKEND-347 / 350 / 348 / 344 / 345 / 346 / 349：leave_service。

後台請假列表、附件短效 URL、家長端小孩請假列表、請假建立 / 取消通知、建立請假、取消請假、
家長上傳附件。BACKEND-357：家長端單筆請假組裝 ``parent_leave_out``（與列表共用）。
"""

import io
import logging
import threading
from collections.abc import Iterator
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, event, func, select, text
from sqlalchemy.orm import Session
from starlette.datastructures import Headers, UploadFile

from app.api.deps import CurrentParent
from app.core.errors import AppError
from app.core.pagination import PageParams
from app.core.storage import Bucket, StorageError
from app.models.attendance import StudentAttendance
from app.models.audit import AuditLog
from app.models.leaves import StudentLeave, StudentLeaveAttachment
from app.models.notifications import Notification
from app.models.parents import ParentAccount
from app.notifications import outbox_jobs
from app.notifications.events import Event
from app.schemas.leaves import (
    AttachmentUrlOut,
    LeaveCreateIn,
    LeaveListQuery,
    LeaveOut,
    ParentLeaveCreateIn,
    ParentLeaveOut,
)
from app.services import leave_service
from app.services.audit_service import Actor
from app.services.leave_service import (
    cancel_leave,
    create_leave,
    get_attachment_url,
    list_child_leaves,
    list_leaves,
    notify_leave_event,
    upload_leave_attachment,
)
from app.services.settings_service import clear_settings_cache
from tests.integration.db.conftest import connect_owner
from tests.support.factories import (
    make_attendance,
    make_class,
    make_class_staff,
    make_guardian,
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


# --- BACKEND-345 create_leave ---


@pytest.fixture
def fresh_settings() -> Iterator[None]:
    clear_settings_cache()
    yield
    clear_settings_cache()


def _parent_leave(student_id: UUID, start: date, end: date | None = None) -> ParentLeaveCreateIn:
    return ParentLeaveCreateIn(
        student_id=student_id, leave_type="sick", start_date=start, end_date=end or start
    )


def _set_leave_window(db: Session, *, past_days: int, future_days: int) -> None:
    # 只改日期窗兩個欄位，保留 leave.window 的其他設定（附件上限等）
    db.execute(
        text(
            "update public.system_settings "
            "set value = value || jsonb_build_object('past_days', :past, 'future_days', :future) "
            "where key = 'leave.window'"
        ),
        {"past": past_days, "future": future_days},
    )
    clear_settings_cache()


def test_create_leave_by_parent(
    db_session: Session, fake_clock: FakeClock, kick_off: None, fresh_settings: None
) -> None:
    ming = make_student(db_session)
    parent = make_parent(db_session)
    make_guardian(db_session, ming, parent=parent)
    make_staff(db_session, permissions=["leaves:read"])

    leave = create_leave(
        db_session,
        _parent_leave(ming.id, date(2026, 9, 1), date(2026, 9, 2)),
        actor=Actor(type="parent", id=parent.id),
        clock=fake_clock,
    )

    assert (leave.created_by_type, leave.created_by_id, leave.status) == (
        "parent",
        parent.id,
        "active",
    )
    rows = db_session.execute(
        select(StudentAttendance).where(StudentAttendance.student_id == ming.id)
    ).scalars()
    # 今天 9/1 套用為 leave；9/2 尚未到，由每日初始化建立
    assert [(r.service_date, r.status, r.leave_id) for r in rows] == [
        (date(2026, 9, 1), "leave", leave.id)
    ]
    assert len(_leave_notifications(db_session, "leave.created", leave.id)) >= 1


def test_create_leave_by_staff_sets_updated_by(
    db_session: Session, fake_clock: FakeClock, kick_off: None, fresh_settings: None
) -> None:
    ming = make_student(db_session)
    staff = make_staff(db_session, permissions=["leaves:write"])
    make_attendance(db_session, ming, service_date=date(2026, 9, 1), status="absent")

    leave = create_leave(
        db_session,
        LeaveCreateIn(
            student_id=ming.id,
            leave_type="personal",
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 1),
        ),
        actor=Actor(type="staff", id=staff.id),
        clock=fake_clock,
    )

    assert (leave.created_by_type, leave.created_by_id) == ("staff", staff.id)
    row = db_session.execute(
        select(StudentAttendance)
        .where(StudentAttendance.student_id == ming.id)
        .execution_options(populate_existing=True)
    ).scalar_one()
    assert (row.status, row.updated_by) == ("leave", staff.id)


def test_create_leave_overlap_409(
    db_session: Session, fake_clock: FakeClock, kick_off: None, fresh_settings: None
) -> None:
    ming = make_student(db_session)
    staff = Actor(type="staff", id=make_staff(db_session).id)
    existing = make_leave(db_session, ming, start_date=date(2026, 9, 2), end_date=date(2026, 9, 4))
    data = LeaveCreateIn(
        student_id=ming.id,
        leave_type="sick",
        start_date=date(2026, 9, 4),
        end_date=date(2026, 9, 5),
    )

    with pytest.raises(AppError) as exc:
        create_leave(db_session, data, actor=staff, clock=fake_clock)
    assert (exc.value.status, exc.value.code) == (409, "leave_overlap")
    assert exc.value.details == {
        "leave_id": existing.id,
        "start_date": date(2026, 9, 2),
        "end_date": date(2026, 9, 4),
    }

    existing.status = "cancelled"
    existing.cancelled_at = datetime(2026, 9, 1, tzinfo=UTC)
    existing.cancelled_by_type = "staff"
    existing.cancelled_by_id = staff.id
    db_session.flush()
    created = create_leave(db_session, data, actor=staff, clock=fake_clock)
    assert created.status == "active"


def test_create_leave_parent_window(
    db_session: Session, fake_clock: FakeClock, kick_off: None, fresh_settings: None
) -> None:
    ming = make_student(db_session)
    parent = make_parent(db_session)
    make_guardian(db_session, ming, parent=parent)
    actor = Actor(type="parent", id=parent.id)
    _set_leave_window(db_session, past_days=30, future_days=60)

    for start in (date(2026, 7, 31), date(2026, 11, 1)):
        with pytest.raises(AppError) as exc:
            create_leave(db_session, _parent_leave(ming.id, start), actor=actor, clock=fake_clock)
        assert (exc.value.status, exc.value.code) == (422, "leave_date_out_of_window")
        assert exc.value.details == {"past_days": 30, "future_days": 60}
    assert (
        create_leave(
            db_session, _parent_leave(ming.id, date(2026, 8, 3)), actor=actor, clock=fake_clock
        ).status
        == "active"
    )

    _set_leave_window(db_session, past_days=0, future_days=60)
    with pytest.raises(AppError) as narrowed:
        create_leave(
            db_session, _parent_leave(ming.id, date(2026, 8, 31)), actor=actor, clock=fake_clock
        )
    assert narrowed.value.code == "leave_date_out_of_window"

    staff_leave = create_leave(
        db_session,
        LeaveCreateIn(
            student_id=ming.id,
            leave_type="sick",
            start_date=date(2026, 7, 31),
            end_date=date(2026, 7, 31),
        ),
        actor=Actor(type="staff", id=make_staff(db_session).id),
        clock=fake_clock,
    )
    assert staff_leave.start_date == date(2026, 7, 31)


def test_create_leave_no_service_days(
    db_session: Session, fake_clock: FakeClock, kick_off: None, fresh_settings: None
) -> None:
    ming = make_student(db_session)

    with pytest.raises(AppError) as exc:
        create_leave(
            db_session,
            LeaveCreateIn(
                student_id=ming.id,
                leave_type="sick",
                start_date=date(2026, 9, 5),
                end_date=date(2026, 9, 6),
            ),
            actor=Actor(type="staff", id=make_staff(db_session).id),
            clock=fake_clock,
        )

    assert (exc.value.status, exc.value.code) == (422, "no_service_days_in_range")


def test_create_leave_parent_idor(
    db_session: Session, fake_clock: FakeClock, kick_off: None, fresh_settings: None
) -> None:
    ming = make_student(db_session)
    other = make_student(db_session, name="林小安")
    parent = make_parent(db_session)
    make_guardian(db_session, ming, parent=parent)
    actor = Actor(type="parent", id=parent.id)

    with pytest.raises(AppError) as idor:
        create_leave(
            db_session, _parent_leave(other.id, date(2026, 9, 2)), actor=actor, clock=fake_clock
        )
    ming.status = "withdrawn"
    ming.withdrawn_on = date(2026, 9, 1)
    db_session.flush()
    with pytest.raises(AppError) as withdrawn:
        create_leave(
            db_session, _parent_leave(ming.id, date(2026, 9, 2)), actor=actor, clock=fake_clock
        )
    suspended = make_student(db_session, status="suspended")
    with pytest.raises(AppError) as staff_inactive:
        create_leave(
            db_session,
            LeaveCreateIn(
                student_id=suspended.id,
                leave_type="sick",
                start_date=date(2026, 9, 2),
                end_date=date(2026, 9, 2),
            ),
            actor=Actor(type="staff", id=make_staff(db_session).id),
            clock=fake_clock,
        )

    assert (idor.value.status, idor.value.code) == (404, "student_not_found")
    assert (withdrawn.value.status, withdrawn.value.code) == (409, "student_not_active")
    assert (staff_inactive.value.status, staff_inactive.value.code) == (409, "student_not_active")


@pytest.fixture
def owner_cleanup() -> Iterator[list[tuple[str, UUID]]]:
    """committing 測試建立的人員以 owner 連線刪除；排在 committing_db_session 之前（先 truncate
    請假 / 出勤 / 通知表，再依 FK 順序刪監護人、學生、家長）。"""
    rows: list[tuple[str, UUID]] = []
    yield rows
    with connect_owner() as conn:
        conn.execute("set lock_timeout = '5s'")
        for table, row_id in rows:
            conn.execute(f"delete from public.{table} where id = %s", (row_id,))  # noqa: S608
        conn.commit()


@pytest.mark.cleanup_tables(
    "student_attendances", "student_leaves", "notification_outbox", "notifications"
)
def test_create_leave_concurrent_duplicate(
    owner_cleanup: list[tuple[str, UUID]],
    committing_db_session: Session,
    db_engine: Engine,
    fake_clock: FakeClock,
    kick_off: None,
    fresh_settings: None,
) -> None:
    ming = make_student(committing_db_session)
    parent = make_parent(committing_db_session)
    guardian = make_guardian(committing_db_session, ming, parent=parent)
    committing_db_session.commit()
    owner_cleanup.extend(
        [("guardians", guardian.id), ("students", ming.id), ("parent_accounts", parent.id)]
    )
    actor = Actor(type="parent", id=parent.id)

    s1 = Session(bind=db_engine)
    s2 = Session(bind=db_engine)
    s2_done = threading.Event()
    outcome: dict[str, object] = {}

    def worker() -> None:
        try:
            outcome["s2"] = create_leave(
                s2, _parent_leave(ming.id, date(2026, 9, 1)), actor=actor, clock=fake_clock
            ).id
            s2.commit()
        except BaseException as exc:
            outcome["s2"] = exc
            s2.rollback()
        finally:
            s2_done.set()

    thread = threading.Thread(target=worker)
    try:
        outcome["s1"] = create_leave(
            s1, _parent_leave(ming.id, date(2026, 9, 1)), actor=actor, clock=fake_clock
        ).id
        thread.start()
        assert not s2_done.wait(timeout=0.5)  # s1 尚未 commit：s2 等待 exclusion constraint 判定
        s1.commit()
        thread.join(timeout=10)
    finally:
        s1.close()
        if thread.is_alive():
            thread.join(timeout=10)
        s2.close()

    error = outcome["s2"]
    assert isinstance(error, AppError)
    assert (error.status, error.code) == (409, "leave_overlap")
    active = committing_db_session.execute(
        select(func.count())
        .select_from(StudentLeave)
        .where(StudentLeave.student_id == ming.id, StudentLeave.status == "active")
    ).scalar_one()
    assert active == 1


# --- BACKEND-346 cancel_leave ---


def _clock_on(d: date) -> FakeClock:
    return FakeClock(datetime(d.year, d.month, d.day, 2, 0, tzinfo=UTC))  # 台北 10:00


def _owned(db: Session, name: str = "王小明") -> tuple[Any, ParentAccount]:
    student = make_student(db, name=name)
    parent = make_parent(db)
    make_guardian(db, student, parent=parent)
    return student, parent


def _rows_by_date(db: Session, student_id: UUID) -> dict[date, StudentAttendance]:
    rows = db.execute(
        select(StudentAttendance)
        .where(StudentAttendance.student_id == student_id)
        .execution_options(populate_existing=True)
    ).scalars()
    return {row.service_date: row for row in rows}


def _leave_rows(db: Session, student: Any, leave: StudentLeave, days: range) -> None:
    for day in days:
        make_attendance(db, student, service_date=date(2026, 9, day), status="leave", leave=leave)


def test_cancel_leave_not_started_whole(db_session: Session, kick_off: None) -> None:
    clock = _clock_on(date(2026, 9, 2))
    ming, parent = _owned(db_session)
    make_staff(db_session, permissions=["leaves:read"])
    leave = make_leave(db_session, ming, start_date=date(2026, 9, 2), end_date=date(2026, 9, 3))
    _leave_rows(db_session, ming, leave, range(2, 3))

    result = cancel_leave(db_session, leave.id, actor=Actor("parent", parent.id), clock=clock)

    assert (result.mode, result.cancelled_from, result.cancelled_to) == (
        "cancelled",
        date(2026, 9, 2),
        date(2026, 9, 3),
    )
    assert result.reverted_dates == [date(2026, 9, 2)]
    assert (result.leave.status, result.leave.cancelled_by_type, result.leave.cancelled_by_id) == (
        "cancelled",
        "parent",
        parent.id,
    )
    assert result.leave.cancelled_at == clock.now()
    row = _rows_by_date(db_session, ming.id)[date(2026, 9, 2)]
    assert (row.status, row.leave_id) == ("expected", None)
    assert len(_leave_notifications(db_session, "leave.cancelled", leave.id)) >= 1


def test_cancel_leave_truncate_midweek(db_session: Session, kick_off: None) -> None:
    clock = _clock_on(date(2026, 9, 9))  # 週三
    ming, parent = _owned(db_session)
    make_staff(db_session, permissions=["leaves:read"])
    leave = make_leave(db_session, ming, start_date=date(2026, 9, 7), end_date=date(2026, 9, 11))
    _leave_rows(db_session, ming, leave, range(7, 12))

    result = cancel_leave(db_session, leave.id, actor=Actor("parent", parent.id), clock=clock)

    assert (result.mode, result.cancelled_from, result.cancelled_to) == (
        "truncated",
        date(2026, 9, 9),
        date(2026, 9, 11),
    )
    assert (result.leave.end_date, result.leave.status) == (date(2026, 9, 8), "active")
    assert result.reverted_dates == [date(2026, 9, 9), date(2026, 9, 10), date(2026, 9, 11)]
    rows = _rows_by_date(db_session, ming.id)
    for day in (9, 10, 11):
        assert (rows[date(2026, 9, day)].status, rows[date(2026, 9, day)].leave_id) == (
            "expected",
            None,
        )
    for day in (7, 8):
        assert (rows[date(2026, 9, day)].status, rows[date(2026, 9, day)].leave_id) == (
            "leave",
            leave.id,
        )
    [notification, *_] = _leave_notifications(db_session, "leave.cancelled", leave.id)
    assert (notification.payload["start_date"], notification.payload["end_date"]) == (
        "2026-09-09",
        "2026-09-11",
    )
    [log] = db_session.execute(
        select(AuditLog).where(
            AuditLog.action == "leave.truncate", AuditLog.entity_id == str(leave.id)
        )
    ).scalars()
    assert (log.actor_type, log.actor_id) == ("parent", parent.id)
    assert (log.before, log.after) == ({"end_date": "2026-09-11"}, {"end_date": "2026-09-08"})


def test_cancel_leave_truncate_keeps_checked_in(db_session: Session, kick_off: None) -> None:
    clock = _clock_on(date(2026, 9, 9))
    ming, parent = _owned(db_session)
    leave = make_leave(db_session, ming, start_date=date(2026, 9, 7), end_date=date(2026, 9, 11))
    _leave_rows(db_session, ming, leave, range(7, 9))
    make_attendance(db_session, ming, service_date=date(2026, 9, 9), status="present")

    result = cancel_leave(db_session, leave.id, actor=Actor("parent", parent.id), clock=clock)

    assert result.mode == "truncated"
    assert result.reverted_dates == []
    assert _rows_by_date(db_session, ming.id)[date(2026, 9, 9)].status == "present"


def test_cancel_leave_already_ended(db_session: Session, kick_off: None) -> None:
    clock = _clock_on(date(2026, 9, 10))
    ming, parent = _owned(db_session)
    staff = make_staff(db_session, permissions=["leaves:write"])
    leave = make_leave(db_session, ming, start_date=date(2026, 9, 7), end_date=date(2026, 9, 8))
    _leave_rows(db_session, ming, leave, range(7, 9))

    for actor in (Actor("parent", parent.id), Actor("staff", staff.id)):
        with pytest.raises(AppError) as exc:
            cancel_leave(db_session, leave.id, actor=actor, clock=clock)
        assert (exc.value.status, exc.value.code) == (409, "leave_already_ended")

    result = cancel_leave(
        db_session, leave.id, actor=Actor("staff", staff.id), scope="all", clock=clock
    )

    assert (result.mode, result.leave.status, result.leave.cancelled_by_type) == (
        "cancelled",
        "cancelled",
        "staff",
    )
    assert result.reverted_dates == [date(2026, 9, 7), date(2026, 9, 8)]
    rows = _rows_by_date(db_session, ming.id)
    assert {rows[date(2026, 9, d)].status for d in (7, 8)} == {"expected"}


def test_cancel_leave_staff_all_started(db_session: Session, kick_off: None) -> None:
    clock = _clock_on(date(2026, 9, 9))
    ming = make_student(db_session)
    staff = make_staff(db_session, permissions=["leaves:write"])
    leave = make_leave(db_session, ming, start_date=date(2026, 9, 7), end_date=date(2026, 9, 11))
    _leave_rows(db_session, ming, leave, range(7, 12))

    result = cancel_leave(
        db_session, leave.id, actor=Actor("staff", staff.id), scope="all", clock=clock
    )

    assert (result.mode, result.leave.status, result.leave.end_date) == (
        "cancelled",
        "cancelled",
        date(2026, 9, 11),
    )
    assert len(result.reverted_dates) == 5
    assert {row.status for row in _rows_by_date(db_session, ming.id).values()} == {"expected"}


def test_cancel_leave_parent_scope_all_rejected(db_session: Session, kick_off: None) -> None:
    ming, parent = _owned(db_session)
    leave = make_leave(db_session, ming, start_date=date(2026, 9, 7))

    with pytest.raises(ValueError, match="scope"):
        cancel_leave(
            db_session,
            leave.id,
            actor=Actor("parent", parent.id),
            scope="all",
            clock=_clock_on(date(2026, 9, 1)),
        )


def test_cancel_leave_not_active(db_session: Session, kick_off: None) -> None:
    ming, parent = _owned(db_session)
    leave = make_leave(db_session, ming, start_date=date(2026, 9, 7), status="cancelled")

    with pytest.raises(AppError) as exc:
        cancel_leave(
            db_session,
            leave.id,
            actor=Actor("parent", parent.id),
            clock=_clock_on(date(2026, 9, 1)),
        )

    assert (exc.value.status, exc.value.code) == (409, "leave_not_active")


def test_cancel_leave_idor(db_session: Session, kick_off: None) -> None:
    clock = _clock_on(date(2026, 9, 1))
    _, parent_a = _owned(db_session)
    other, _ = _owned(db_session, name="林小安")
    leave = make_leave(db_session, other, start_date=date(2026, 9, 7))

    with pytest.raises(AppError) as idor:
        cancel_leave(db_session, leave.id, actor=Actor("parent", parent_a.id), clock=clock)
    with pytest.raises(AppError) as missing:
        cancel_leave(db_session, uuid4(), actor=Actor("parent", parent_a.id), clock=clock)

    assert (idor.value.status, idor.value.code) == (404, "leave_not_found")
    assert (idor.value.status, idor.value.code, idor.value.message) == (
        missing.value.status,
        missing.value.code,
        missing.value.message,
    )
    db_session.refresh(leave)
    assert leave.status == "active"


# --- BACKEND-349 upload_leave_attachment ---

_PDF = b"%PDF-1.7\n" + b"0" * 50
_JPEG = b"\xff\xd8\xff\xe0" + b"0" * 100
_ZIP = b"PK\x03\x04" + b"0" * 50


def _upload(
    content: bytes, filename: str = "診斷證明.pdf", content_type: str = "application/pdf"
) -> UploadFile:
    return UploadFile(
        file=io.BytesIO(content), filename=filename, headers=Headers({"content-type": content_type})
    )


def _current_parent(parent: ParentAccount) -> CurrentParent:
    return CurrentParent(
        id=parent.id,
        line_user_id=parent.line_user_id,
        display_name=parent.display_name,
        token_version=parent.token_version,
    )


def _attachment_count(db: Session, leave_id: UUID) -> int:
    return db.execute(
        select(func.count())
        .select_from(StudentLeaveAttachment)
        .where(StudentLeaveAttachment.leave_id == leave_id)
    ).scalar_one()


def test_upload_leave_attachment_success(db_session: Session, fresh_settings: None) -> None:
    storage = FakeStorage()
    ming, parent = _owned(db_session)
    leave = make_leave(db_session, ming, start_date=date(2026, 9, 7))

    out = upload_leave_attachment(
        db_session,
        leave.id,
        _upload(_PDF),
        parent=_current_parent(parent),
        storage=storage,
        clock=_clock_on(date(2026, 9, 1)),
    )

    assert (out.mime_type, out.size_bytes) == ("application/pdf", len(_PDF))
    assert out.url is not None
    assert out.url.startswith("https://storage.test/leave-attachments/")
    [(bucket, path)] = list(storage.objects)
    assert (bucket, storage.objects[(bucket, path)]) == ("leave-attachments", _PDF)
    row = db_session.get(StudentLeaveAttachment, out.id)
    assert row is not None
    assert row.storage_path == path
    assert path.startswith(f"{leave.id}/")
    assert path.endswith(".pdf")
    assert "診斷" not in path


def test_upload_leave_attachment_rules(db_session: Session, fresh_settings: None) -> None:
    storage = FakeStorage()
    clock = _clock_on(date(2026, 9, 1))
    ming, parent = _owned(db_session)
    other, _ = _owned(db_session, name="林小安")
    others_leave = make_leave(db_session, other, start_date=date(2026, 9, 7))
    cancelled = make_leave(db_session, ming, start_date=date(2026, 9, 14), status="cancelled")
    full = make_leave(db_session, ming, start_date=date(2026, 9, 7))
    make_leave_attachment(db_session, full)
    make_leave_attachment(db_session, full)
    db_session.execute(
        text(
            "update public.system_settings "
            "set value = value || jsonb_build_object('max_attachments', 2) "
            "where key = 'leave.window'"
        )
    )
    clear_settings_cache()
    me = _current_parent(parent)

    def upload(leave_id: UUID) -> pytest.ExceptionInfo[AppError]:
        with pytest.raises(AppError) as exc:
            upload_leave_attachment(
                db_session, leave_id, _upload(_PDF), parent=me, storage=storage, clock=clock
            )
        return exc

    idor = upload(others_leave.id)
    missing = upload(uuid4())
    inactive = upload(cancelled.id)
    limited = upload(full.id)

    assert (idor.value.status, idor.value.code) == (404, "leave_not_found")
    assert (missing.value.status, missing.value.code, missing.value.message) == (
        404,
        "leave_not_found",
        idor.value.message,
    )
    assert (inactive.value.status, inactive.value.code) == (409, "leave_not_active")
    assert (limited.value.status, limited.value.code) == (409, "attachment_limit_reached")
    assert limited.value.details == {"max_attachments": 2}
    assert storage.objects == {}


def test_upload_leave_attachment_file_checks(db_session: Session, fresh_settings: None) -> None:
    storage = FakeStorage()
    clock = _clock_on(date(2026, 9, 1))
    ming, parent = _owned(db_session)
    leave = make_leave(db_session, ming, start_date=date(2026, 9, 7))
    db_session.execute(
        text(
            "update public.system_settings "
            "set value = value || jsonb_build_object('max_attachment_mb', 1) "
            "where key = 'leave.window'"
        )
    )
    clear_settings_cache()
    me = _current_parent(parent)

    with pytest.raises(AppError) as unsupported:
        upload_leave_attachment(
            db_session, leave.id, _upload(_ZIP, "a.pdf"), parent=me, storage=storage, clock=clock
        )
    big = _JPEG + b"0" * (2 * 1024 * 1024)
    with pytest.raises(AppError) as too_large:
        upload_leave_attachment(
            db_session,
            leave.id,
            _upload(big, "a.jpg", "image/jpeg"),
            parent=me,
            storage=storage,
            clock=clock,
        )

    assert (unsupported.value.status, unsupported.value.code) == (415, "unsupported_file_type")
    assert (too_large.value.status, too_large.value.code) == (413, "file_too_large")
    assert too_large.value.details == {"max_bytes": 1024 * 1024}
    assert (storage.objects, _attachment_count(db_session, leave.id)) == ({}, 0)


def test_upload_leave_attachment_storage_error(db_session: Session, fresh_settings: None) -> None:
    storage = FakeStorage()
    storage.upload_error = StorageError("S3 put_object 失敗")
    ming, parent = _owned(db_session)
    leave = make_leave(db_session, ming, start_date=date(2026, 9, 7))

    with pytest.raises(AppError) as exc:
        upload_leave_attachment(
            db_session,
            leave.id,
            _upload(_PDF),
            parent=_current_parent(parent),
            storage=storage,
            clock=_clock_on(date(2026, 9, 1)),
        )

    assert (exc.value.status, exc.value.code) == (502, "storage_unavailable")
    assert _attachment_count(db_session, leave.id) == 0


# --- BACKEND-357 parent_leave_out（家長端單筆請假組裝；list_child_leaves 與取消 endpoint 共用）---


class _SignFailsFor(FakeStorage):
    """只有指定路徑的簽名失敗，其餘照常簽名。"""

    def __init__(self, bad_paths: set[str]) -> None:
        super().__init__()
        self.bad_paths = bad_paths

    def create_signed_url(self, bucket: Bucket, path: str, expires_in: int = 300) -> str:
        if path in self.bad_paths:
            raise StorageError("S3 generate_presigned_url 失敗")
        return super().create_signed_url(bucket, path, expires_in)


def _signed(path: str) -> str:
    return f"https://storage.test/leave-attachments/{path}?exp=300"


def test_parent_leave_out_fields(db_session: Session, fake_clock: FakeClock) -> None:
    ming = make_student(db_session, name="王小明")
    leave = make_leave(
        db_session,
        ming,
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 3),
        leave_type="personal",
        reason="家裡有事",
        created_by_type="parent",
    )

    out = leave_service.parent_leave_out(leave, storage=FakeStorage(), clock=fake_clock)

    assert out == ParentLeaveOut(
        id=leave.id,
        student_id=ming.id,
        leave_type="personal",
        leave_type_label="事假",
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 3),
        reason="家裡有事",
        status="active",
        created_by_type="parent",
        created_at=leave.created_at,
        cancelled_at=None,
        can_cancel=True,
        attachments=[],
    )
    # 家長端不回傳員工姓名與取消者資訊
    assert not {"created_by_name", "cancelled_by_name", "cancelled_by_type", "student"} & set(
        out.model_dump()
    )


def test_parent_leave_out_attachment_urls(db_session: Session, fake_clock: FakeClock) -> None:
    ming = make_student(db_session)
    leave = make_leave(db_session, ming, start_date=date(2026, 9, 1))
    jpg = make_leave_attachment(db_session, leave, ext="jpg")
    pdf = make_leave_attachment(db_session, leave, ext="pdf")
    db_session.expire_all()

    out = leave_service.parent_leave_out(leave, storage=FakeStorage(), clock=fake_clock)

    assert len(out.attachments) == 2
    assert {a.id: (a.mime_type, a.size_bytes, a.url) for a in out.attachments} == {
        jpg.id: ("image/jpeg", 1024, _signed(jpg.storage_path)),
        pdf.id: ("application/pdf", 1024, _signed(pdf.storage_path)),
    }
    # 簽名 URL，不是 null 也不是空字串
    assert all(a.url for a in out.attachments)
    assert "storage_path" not in out.attachments[0].model_dump()


def test_parent_leave_out_single_signing_failure(
    db_session: Session, fake_clock: FakeClock, caplog: pytest.LogCaptureFixture
) -> None:
    ming = make_student(db_session)
    leave = make_leave(db_session, ming, start_date=date(2026, 9, 1))
    good = make_leave_attachment(db_session, leave, ext="jpg")
    bad = make_leave_attachment(db_session, leave, ext="pdf")
    db_session.expire_all()
    storage = _SignFailsFor({bad.storage_path})

    with caplog.at_level(logging.WARNING, logger="app.services.leave_service"):
        out = leave_service.parent_leave_out(leave, storage=storage, clock=fake_clock)

    # 單一附件簽名失敗：該附件 url 為 None，其餘附件與整體照常，不拋例外
    assert {a.id: a.url for a in out.attachments} == {
        good.id: _signed(good.storage_path),
        bad.id: None,
    }
    assert (out.id, out.status, out.can_cancel) == (leave.id, "active", True)
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any(str(bad.id) in message for message in warnings)
    assert not any(str(good.id) in message for message in warnings)
    assert "generate_presigned_url" not in caplog.text  # 上游錯誤文字不進 log


def test_parent_leave_out_can_cancel_boundaries(db_session: Session) -> None:
    clock = FakeClock(datetime(2026, 9, 2, 2, 0, tzinfo=UTC))  # 台北 9/2 10:00
    leaves = {
        "ends_today": make_leave(
            db_session,
            make_student(db_session),
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 2),
        ),
        "starts_today": make_leave(
            db_session, make_student(db_session), start_date=date(2026, 9, 2)
        ),
        "future": make_leave(db_session, make_student(db_session), start_date=date(2026, 9, 10)),
        "ended_yesterday": make_leave(
            db_session, make_student(db_session), start_date=date(2026, 9, 1)
        ),
        "cancelled_future": make_leave(
            db_session, make_student(db_session), start_date=date(2026, 9, 10), status="cancelled"
        ),
        "cancelled_ends_today": make_leave(
            db_session,
            make_student(db_session),
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 2),
            status="cancelled",
        ),
    }

    got = {
        name: leave_service.parent_leave_out(leave, storage=FakeStorage(), clock=clock).can_cancel
        for name, leave in leaves.items()
    }

    assert got == {
        "ends_today": True,  # 今天是最後一天仍可取消（取消今天）
        "starts_today": True,
        "future": True,
        "ended_yesterday": False,
        "cancelled_future": False,
        "cancelled_ends_today": False,
    }


def test_parent_leave_out_can_cancel_flips_at_taipei_midnight(db_session: Session) -> None:
    ming = make_student(db_session)
    leave = make_leave(db_session, ming, start_date=date(2026, 9, 1))
    clock = FakeClock(datetime(2026, 9, 1, 15, 30, tzinfo=UTC))  # 台北 9/1 23:30

    before = leave_service.parent_leave_out(leave, storage=FakeStorage(), clock=clock)
    clock.set(datetime(2026, 9, 1, 16, 30, tzinfo=UTC))  # 台北 9/2 00:30
    after = leave_service.parent_leave_out(leave, storage=FakeStorage(), clock=clock)

    assert (before.can_cancel, after.can_cancel) == (True, False)


def test_parent_leave_out_matches_list_child_leaves(
    db_session: Session, fake_clock: FakeClock
) -> None:
    """列表的每一筆就是單筆組裝的結果：兩邊不會各長各的。"""
    storage = FakeStorage()
    ming = make_student(db_session)
    ended = make_leave(db_session, ming, start_date=date(2026, 8, 20), reason="感冒")
    ongoing = make_leave(
        db_session,
        ming,
        start_date=date(2026, 8, 31),
        end_date=date(2026, 9, 2),
        leave_type="other",
    )
    cancelled = make_leave(db_session, ming, start_date=date(2026, 9, 10), status="cancelled")
    make_leave_attachment(db_session, ongoing, ext="jpg")
    make_leave_attachment(db_session, ongoing, ext="pdf")
    db_session.expire_all()

    page = list_child_leaves(db_session, ming.id, _PAGE, storage=storage, clock=fake_clock)

    assert [row.id for row in page.items] == [cancelled.id, ongoing.id, ended.id]
    assert [len(row.attachments) for row in page.items] == [0, 2, 0]
    leaves = {leave.id: leave for leave in (ended, ongoing, cancelled)}
    for row in page.items:
        single = leave_service.parent_leave_out(leaves[row.id], storage=storage, clock=fake_clock)
        assert row == single


def test_parent_leave_out_list_child_leaves_query_count(
    db_session: Session, fake_clock: FakeClock
) -> None:
    """列表的 SQL 次數固定（count、請假、附件 selectin），不隨筆數與附件數增加。"""
    ming = make_student(db_session)
    for index in range(10):
        leave = make_leave(db_session, ming, start_date=date(2026, 9, 1 + 2 * index))
        make_leave_attachment(db_session, leave, ext="jpg")
        make_leave_attachment(db_session, leave, ext="pdf")
    student_id = ming.id
    db_session.expire_all()
    statements: list[str] = []

    def record(_conn: object, _cur: object, statement: str, *_args: object) -> None:
        statements.append(statement)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", record)
    try:
        page = list_child_leaves(
            db_session, student_id, _PAGE, storage=FakeStorage(), clock=fake_clock
        )
    finally:
        event.remove(engine, "before_cursor_execute", record)

    assert page.total == 10
    assert sum(len(row.attachments) for row in page.items) == 20
    assert all(attachment.url for row in page.items for attachment in row.attachments)
    assert len(statements) == 3
