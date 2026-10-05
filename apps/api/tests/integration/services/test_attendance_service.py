"""attendance_service（domain_spec M4）。

- BACKEND-302：``ensure_attendance_row``（取得或建立出勤列，冪等、並發安全）。
"""

import threading
from collections.abc import Iterator
from datetime import date
from uuid import UUID

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.models.attendance import StudentAttendance
from app.services.attendance_service import ensure_attendance_row
from tests.integration.db.conftest import connect_owner
from tests.support.factories import make_attendance, make_leave, make_student

_DAY = date(2026, 9, 1)


def _attendance_count(session: Session, student_id: UUID) -> int:
    return session.execute(
        select(func.count())
        .select_from(StudentAttendance)
        .where(StudentAttendance.student_id == student_id)
    ).scalar_one()


@pytest.fixture
def owner_cleanup_students() -> Iterator[list[UUID]]:
    """committing 測試建立的學生以 owner 連線刪除；排在 committing_db_session 之前
    （先 close session、truncate 出勤表，再刪學生）。"""
    ids: list[UUID] = []
    yield ids
    with connect_owner() as conn:
        conn.execute("set lock_timeout = '5s'")
        for student_id in ids:
            conn.execute("delete from public.students where id = %s", (student_id,))
        conn.commit()


# --- BACKEND-302 ensure_attendance_row ---


def test_ensure_row_creates_expected(db_session: Session) -> None:
    student = make_student(db_session)

    row = ensure_attendance_row(db_session, student.id, _DAY)

    assert (row.student_id, row.service_date) == (student.id, _DAY)
    assert row.status == "expected"
    assert row.leave_id is None
    assert _attendance_count(db_session, student.id) == 1


def test_ensure_row_creates_leave_when_on_leave(db_session: Session) -> None:
    student = make_student(db_session)
    leave = make_leave(db_session, student, start_date=_DAY, end_date=date(2026, 9, 2))
    # 已取消的請假不算
    other = make_student(db_session, name="陳小華")
    make_leave(db_session, other, start_date=_DAY, status="cancelled")

    row = ensure_attendance_row(db_session, student.id, _DAY)
    other_row = ensure_attendance_row(db_session, other.id, _DAY)
    # 請假區間外的日子仍為 expected
    after = ensure_attendance_row(db_session, student.id, date(2026, 9, 3))

    assert (row.status, row.leave_id) == ("leave", leave.id)
    assert (other_row.status, other_row.leave_id) == ("expected", None)
    assert (after.status, after.leave_id) == ("expected", None)


def test_ensure_row_idempotent(db_session: Session) -> None:
    student = make_student(db_session)
    existing = make_attendance(db_session, student, service_date=_DAY, status="present")
    check_in_at = existing.check_in_at

    row = ensure_attendance_row(db_session, student.id, _DAY)
    again = ensure_attendance_row(db_session, student.id, _DAY, for_update=False)

    assert row.id == existing.id
    assert again.id == existing.id
    assert (row.status, row.check_in_at) == ("present", check_in_at)
    assert _attendance_count(db_session, student.id) == 1


@pytest.mark.cleanup_tables("student_attendances")
def test_ensure_row_concurrent_single_row(
    owner_cleanup_students: list[UUID], committing_db_session: Session, db_engine: Engine
) -> None:
    """s1 建立後未 commit；s2 以執行緒呼叫會等待 → s1 commit 後 s2 拿到同一列，最終只有一列。"""
    student = make_student(committing_db_session)
    committing_db_session.commit()
    owner_cleanup_students.append(student.id)

    s1 = Session(bind=db_engine)
    s2 = Session(bind=db_engine)
    s2_done = threading.Event()
    result: dict[str, UUID] = {}
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            result["s2"] = ensure_attendance_row(s2, student.id, _DAY).id
            s2.commit()
        except BaseException as exc:
            errors.append(exc)
        finally:
            s2_done.set()

    thread = threading.Thread(target=worker)
    try:
        s1_id = ensure_attendance_row(s1, student.id, _DAY).id
        thread.start()
        assert not s2_done.wait(timeout=0.5), errors  # s1 尚未 commit：s2 被擋住
        s1.commit()
        thread.join(timeout=10)
    finally:
        s1.close()
        if thread.is_alive():
            thread.join(timeout=10)
        s2.close()

    assert errors == []
    assert result["s2"] == s1_id
    assert _attendance_count(committing_db_session, student.id) == 1
