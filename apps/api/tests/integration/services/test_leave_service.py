"""BACKEND-347 / 350：leave_service（後台請假列表、附件短效 URL）。"""

from datetime import UTC, date, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.pagination import PageParams
from app.core.storage import StorageError
from app.schemas.leaves import AttachmentUrlOut, LeaveListQuery, LeaveOut
from app.services.leave_service import get_attachment_url, list_leaves
from tests.support.factories import (
    make_class,
    make_leave,
    make_leave_attachment,
    make_parent,
    make_staff,
    make_student,
)
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
