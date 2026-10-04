"""BACKEND-373 / 384：homework_service（進度列鎖定、家長端當日作業明細）。"""

import threading
from collections.abc import Iterator
from datetime import date, time
from uuid import UUID

import pytest
from sqlalchemy import Engine, func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.models.homework import HomeworkDailyProgress
from app.models.reference import Subject
from app.services.homework_service import get_child_homework, lock_progress_row
from tests.integration.db.conftest import connect_owner
from tests.support.factories import (
    make_homework_item,
    make_homework_progress,
    make_student,
)

_DAY = date(2026, 9, 1)


def _progress_count(session: Session, student_id: UUID) -> int:
    return session.execute(
        select(func.count())
        .select_from(HomeworkDailyProgress)
        .where(HomeworkDailyProgress.student_id == student_id)
    ).scalar_one()


def test_lock_progress_row_creates(db_session: Session) -> None:
    student = make_student(db_session)

    first = lock_progress_row(db_session, student.id, _DAY)
    second = lock_progress_row(db_session, student.id, _DAY)

    assert first.overall_status == "not_started"
    assert (first.student_id, first.service_date) == (student.id, _DAY)
    assert (first.ready_eta, first.note) == (None, None)
    assert second.id == first.id
    assert _progress_count(db_session, student.id) == 1
    # 另一天是另一列
    other_day = lock_progress_row(db_session, student.id, date(2026, 9, 2))
    assert other_day.id != first.id
    assert _progress_count(db_session, student.id) == 2


def test_lock_progress_row_returns_existing_row_untouched(db_session: Session) -> None:
    student = make_student(db_session)
    existing = make_homework_progress(
        db_session,
        student,
        service_date=_DAY,
        overall_status="in_progress",
        ready_eta=time(17, 30),
        note="剩數學訂正",
    )

    row = lock_progress_row(db_session, student.id, _DAY)

    assert row.id == existing.id
    assert (row.overall_status, row.ready_eta, row.note) == (
        "in_progress",
        time(17, 30),
        "剩數學訂正",
    )
    assert _progress_count(db_session, student.id) == 1


@pytest.fixture
def owner_cleanup_students() -> Iterator[list[UUID]]:
    """committing 測試建立的學生以 owner 連線刪除；排在 committing_db_session 之前
    （先 close session、truncate 進度表，再刪學生）。"""
    ids: list[UUID] = []
    yield ids
    with connect_owner() as conn:
        conn.execute("set lock_timeout = '5s'")
        for student_id in ids:
            conn.execute("delete from public.students where id = %s", (student_id,))
        conn.commit()


@pytest.mark.cleanup_tables("homework_daily_progress")
def test_lock_progress_row_blocks(
    owner_cleanup_students: list[UUID], committing_db_session: Session, db_engine: Engine
) -> None:
    """無列時：s1 建立後未 commit，s2 的 INSERT 要等 s1 → lock_timeout 內拋 LockNotAvailable。"""
    student = make_student(committing_db_session)
    committing_db_session.commit()
    owner_cleanup_students.append(student.id)

    s1 = Session(bind=db_engine)
    s2 = Session(bind=db_engine)
    try:
        created = lock_progress_row(s1, student.id, _DAY)
        s2.execute(text("set lock_timeout = '200ms'"))
        with pytest.raises(OperationalError) as blocked:
            lock_progress_row(s2, student.id, _DAY)
        assert getattr(blocked.value.orig, "sqlstate", None) == "55P03"  # lock_not_available
        s2.rollback()

        s1.commit()
        s2.execute(text("set lock_timeout = '200ms'"))
        retried = lock_progress_row(s2, student.id, _DAY)
        s2.commit()
    finally:
        s1.close()
        s2.close()

    assert retried.id == created.id


@pytest.mark.cleanup_tables("homework_daily_progress")
def test_lock_progress_row_blocks_existing_row(
    owner_cleanup_students: list[UUID], committing_db_session: Session, db_engine: Engine
) -> None:
    """已有列時：s1 持有 FOR UPDATE，s2 的 SELECT FOR UPDATE 被擋；s1 commit 後才能取得。"""
    student = make_student(committing_db_session)
    existing = make_homework_progress(committing_db_session, student, service_date=_DAY)
    committing_db_session.commit()
    owner_cleanup_students.append(student.id)

    s1 = Session(bind=db_engine)
    s2 = Session(bind=db_engine)
    try:
        assert lock_progress_row(s1, student.id, _DAY).id == existing.id
        s2.execute(text("set lock_timeout = '200ms'"))
        with pytest.raises(OperationalError) as blocked:
            lock_progress_row(s2, student.id, _DAY)
        assert getattr(blocked.value.orig, "sqlstate", None) == "55P03"
        s2.rollback()

        s1.commit()
        retried = lock_progress_row(s2, student.id, _DAY)
        s2.commit()
    finally:
        s1.close()
        s2.close()

    assert retried.id == existing.id


@pytest.mark.cleanup_tables("homework_daily_progress")
def test_lock_progress_row_concurrent_create_yields_one_row(
    owner_cleanup_students: list[UUID], committing_db_session: Session, db_engine: Engine
) -> None:
    """同一學生同一日兩個請求同時建立：只有一列，第二個等第一個 commit 後拿到同一列。"""
    student = make_student(committing_db_session)
    committing_db_session.commit()
    owner_cleanup_students.append(student.id)

    a_locked = threading.Event()
    release_a = threading.Event()
    b_done = threading.Event()
    ids: dict[str, UUID] = {}
    errors: list[BaseException] = []

    def worker_a() -> None:
        sa = Session(bind=db_engine)
        try:
            ids["a"] = lock_progress_row(sa, student.id, _DAY).id
            a_locked.set()
            release_a.wait(timeout=10)
            sa.commit()
        except BaseException as exc:
            errors.append(exc)
            a_locked.set()
        finally:
            sa.close()

    def worker_b() -> None:
        sb = Session(bind=db_engine)
        try:
            a_locked.wait(timeout=10)
            ids["b"] = lock_progress_row(sb, student.id, _DAY).id
            sb.commit()
        except BaseException as exc:
            errors.append(exc)
        finally:
            sb.close()
            b_done.set()

    threads = [threading.Thread(target=worker_a), threading.Thread(target=worker_b)]
    for t in threads:
        t.start()
    try:
        assert a_locked.wait(timeout=10)
        assert not b_done.wait(timeout=0.5)  # A 尚未 commit：B 被擋住
    finally:
        release_a.set()
        for t in threads:
            t.join(timeout=10)

    assert errors == []
    assert b_done.is_set()
    assert ids["a"] == ids["b"]
    with Session(bind=db_engine) as check:
        assert _progress_count(check, student.id) == 1


def _subject(db: Session, name: str) -> Subject:
    return db.execute(select(Subject).where(Subject.name == name)).scalar_one()


def test_child_homework_detail(db_session: Session) -> None:
    ming = make_student(db_session, name="王小明")
    other = make_student(db_session, name="林小安")
    make_homework_item(
        db_session,
        ming,
        service_date=_DAY,
        title="數學習作 p.12",
        status="doing",
        subject=_subject(db_session, "數學"),
        sort_order=20,
    )
    make_homework_item(
        db_session,
        ming,
        service_date=_DAY,
        title="國語生字",
        status="done",
        subject=_subject(db_session, "國語"),
        sort_order=10,
    )
    make_homework_item(db_session, ming, service_date=_DAY, title="自訂項目", sort_order=30)
    make_homework_item(db_session, ming, service_date=date(2026, 9, 2), title="隔天的作業")
    make_homework_item(db_session, other, service_date=_DAY, title="別人的作業")
    progress = make_homework_progress(
        db_session,
        ming,
        service_date=_DAY,
        overall_status="in_progress",
        ready_eta=time(17, 30),
        note="剩數學訂正",
    )
    make_homework_progress(db_session, other, service_date=_DAY, overall_status="done")
    db_session.flush()

    out = get_child_homework(db_session, ming.id, _DAY)

    assert (out.student_id, out.date) == (ming.id, _DAY)
    assert [(i.title, i.subject_name, i.status) for i in out.items] == [
        ("國語生字", "國語", "done"),
        ("數學習作 p.12", "數學", "doing"),
        ("自訂項目", None, "todo"),
    ]
    assert (out.overall_status, out.ready_eta, out.note) == ("in_progress", "17:30", "剩數學訂正")
    assert out.updated_at == progress.updated_at
    # 家長端不含內部欄位
    assert set(out.items[0].model_dump()) == {"title", "subject_name", "status"}
    assert set(out.model_dump()) == {
        "student_id",
        "date",
        "items",
        "overall_status",
        "ready_eta",
        "note",
        "updated_at",
    }


def test_child_homework_without_progress_row(db_session: Session) -> None:
    ming = make_student(db_session)
    item = make_homework_item(db_session, ming, service_date=_DAY, title="國語生字")
    db_session.flush()

    out = get_child_homework(db_session, ming.id, _DAY)

    assert [i.title for i in out.items] == ["國語生字"]
    assert (out.overall_status, out.ready_eta, out.note) == ("not_started", None, None)
    assert out.updated_at == item.updated_at


def test_child_homework_progress_only(db_session: Session) -> None:
    ming = make_student(db_session)
    progress = make_homework_progress(
        db_session, ming, service_date=_DAY, overall_status="done", note="都完成了"
    )
    db_session.flush()

    out = get_child_homework(db_session, ming.id, _DAY)

    assert out.items == []
    assert (out.overall_status, out.note, out.updated_at) == (
        "done",
        "都完成了",
        progress.updated_at,
    )


def test_child_homework_empty(db_session: Session) -> None:
    ming = make_student(db_session)

    out = get_child_homework(db_session, ming.id, _DAY)

    assert out.items == []
    assert (out.student_id, out.date) == (ming.id, _DAY)
    assert (out.overall_status, out.ready_eta, out.note, out.updated_at) == (
        "not_started",
        None,
        None,
        None,
    )
