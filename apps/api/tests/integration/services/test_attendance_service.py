"""attendance_service（domain_spec M4）。

- BACKEND-302：``ensure_attendance_row``（取得或建立出勤列，冪等、並發安全）。
- BACKEND-310：``amend_attendance``（改判，寫 audit、commit 後推播）。
"""

import threading
from collections.abc import Iterator
from datetime import date, datetime, time
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.api.deps import CurrentStaff
from app.core.clock import combine_taipei
from app.core.errors import AppError
from app.core.request_meta import RequestMeta
from app.core.tx_hooks import install_tx_hooks
from app.models.account import StaffUser
from app.models.attendance import StudentAttendance
from app.models.audit import AuditLog
from app.realtime import publish as publish_module
from app.realtime.publish import admin_topic_channel, student_channel
from app.schemas.attendance import AttendanceAmendIn
from app.services.attendance_service import amend_attendance, ensure_attendance_row
from tests.integration.db.conftest import connect_owner
from tests.support.factories import make_attendance, make_leave, make_staff, make_student
from tests.support.fake_clock import FakeClock

_DAY = date(2026, 9, 1)
_META = RequestMeta(ip="203.0.113.5", user_agent="pytest", request_id=None)

Call = tuple[list[str], dict[str, Any]]


def _taipei(d: date, hour: int, minute: int = 0) -> datetime:
    return combine_taipei(d, time(hour, minute))


def _current(staff: StaffUser) -> CurrentStaff:
    return CurrentStaff(
        id=staff.id,
        username=staff.username,
        display_name=staff.display_name,
        role_id=staff.role.id,
        role_code=staff.role.code,
        role_name=staff.role.name,
        permissions=frozenset(staff.role.permissions),
        must_change_password=staff.must_change_password,
        token_version=staff.token_version,
    )


@pytest.fixture
def actor(db_session: Session) -> CurrentStaff:
    return _current(make_staff(db_session, permissions=["attendance:amend"], display_name="陳主任"))


@pytest.fixture
def published(monkeypatch: pytest.MonkeyPatch) -> list[Call]:
    install_tx_hooks()
    calls: list[Call] = []

    def record(channels: list[str], message: dict[str, Any]) -> None:
        calls.append((list(channels), dict(message)))

    monkeypatch.setattr(publish_module, "publish_threadsafe", record)
    return calls


def _amend_logs(session: Session, attendance_id: UUID) -> list[AuditLog]:
    return list(
        session.execute(
            select(AuditLog).where(
                AuditLog.action == "attendance.amend", AuditLog.entity_id == str(attendance_id)
            )
        ).scalars()
    )


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


# --- BACKEND-310 amend_attendance ---


def _amend(
    session: Session, row_id: UUID, actor: CurrentStaff, clock: FakeClock, **fields: Any
) -> Any:
    return amend_attendance(
        session, row_id, AttendanceAmendIn(**fields), actor=actor, meta=_META, clock=clock
    )


def _error(exc: pytest.ExceptionInfo[AppError]) -> tuple[int, str]:
    return exc.value.status, exc.value.code


def test_amend_present_to_expected(
    db_session: Session, actor: CurrentStaff, fake_clock: FakeClock
) -> None:
    student = make_student(db_session, name="王小明")
    row = make_attendance(db_session, student, service_date=_DAY, status="present", note="早到")

    out = _amend(db_session, row.id, actor, fake_clock, status="expected", reason="誤刷")

    assert (out.id, out.status, out.student_name) == (row.id, "expected", "王小明")
    assert (out.check_in_at, out.check_in_source) == (None, None)
    assert (out.check_out_at, out.check_out_source) == (None, None)
    db_row = db_session.get(StudentAttendance, row.id)
    assert db_row is not None
    assert (db_row.status, db_row.check_in_at, db_row.check_in_source) == ("expected", None, None)
    assert db_row.updated_by == actor.id
    [log] = _amend_logs(db_session, row.id)
    assert (log.actor_type, log.actor_id, log.entity_type) == (
        "staff",
        actor.id,
        "student_attendance",
    )
    assert log.before == {
        "status": "present",
        "check_in_at": _taipei(_DAY, 15).isoformat(),
        "check_out_at": None,
        "note": "早到",
    }
    assert log.after == {
        "status": "expected",
        "check_in_at": None,
        "check_out_at": None,
        "note": "早到",
        "reason": "誤刷",
    }
    assert log.ip == "203.0.113.5"


def test_amend_to_left_requires_times(
    db_session: Session, actor: CurrentStaff, fake_clock: FakeClock
) -> None:
    student = make_student(db_session)
    row = make_attendance(db_session, student, service_date=_DAY)

    with pytest.raises(AppError) as missing_out:
        _amend(
            db_session,
            row.id,
            actor,
            fake_clock,
            status="left",
            check_in_at=_taipei(_DAY, 15),
            reason="補登",
        )
    assert _error(missing_out) == (422, "check_out_required")

    with pytest.raises(AppError) as reversed_times:
        _amend(
            db_session,
            row.id,
            actor,
            fake_clock,
            status="left",
            check_in_at=_taipei(_DAY, 15),
            check_out_at=_taipei(_DAY, 14),
            reason="補登",
        )
    assert _error(reversed_times) == (422, "invalid_times")

    out = _amend(
        db_session,
        row.id,
        actor,
        fake_clock,
        status="left",
        check_in_at=_taipei(_DAY, 15),
        check_out_at=_taipei(_DAY, 18),
        reason="補登",
    )
    assert out.status == "left"
    assert (out.check_in_at, out.check_out_at) == (_taipei(_DAY, 15), _taipei(_DAY, 18))
    assert (out.check_in_source, out.check_out_source) == ("manual", "manual")


def test_amend_to_present_requires_check_in(
    db_session: Session, actor: CurrentStaff, fake_clock: FakeClock
) -> None:
    student = make_student(db_session)
    row = make_attendance(db_session, student, service_date=_DAY, status="absent")

    with pytest.raises(AppError) as missing_in:
        _amend(db_session, row.id, actor, fake_clock, status="present", reason="補登")
    assert _error(missing_in) == (422, "check_in_required")


def test_amend_left_to_present_clears_check_out(
    db_session: Session, actor: CurrentStaff, fake_clock: FakeClock
) -> None:
    student = make_student(db_session)
    row = make_attendance(
        db_session, student, service_date=_DAY, status="left", check_in_source="nfc"
    )

    out = _amend(db_session, row.id, actor, fake_clock, status="present", reason="還沒離班")

    assert out.status == "present"
    # 簽到時間沿用原值、來源未變動保留 nfc；離班時間與來源清空
    assert (out.check_in_at, out.check_in_source) == (_taipei(_DAY, 15), "nfc")
    assert (out.check_out_at, out.check_out_source) == (None, None)


def test_amend_changed_time_side_becomes_manual(
    db_session: Session, actor: CurrentStaff, fake_clock: FakeClock
) -> None:
    student = make_student(db_session)
    row = make_attendance(
        db_session,
        student,
        service_date=_DAY,
        status="left",
        check_in_source="nfc",
        check_out_source="pickup",
    )

    out = _amend(
        db_session, row.id, actor, fake_clock, check_out_at=_taipei(_DAY, 17, 30), reason="更正"
    )

    assert out.status == "left"
    assert (out.check_in_at, out.check_in_source) == (_taipei(_DAY, 15), "nfc")
    assert (out.check_out_at, out.check_out_source) == (_taipei(_DAY, 17, 30), "manual")


def test_amend_null_status_treated_as_not_given(
    db_session: Session, actor: CurrentStaff, fake_clock: FakeClock
) -> None:
    student = make_student(db_session)
    row = make_attendance(db_session, student, service_date=_DAY, status="present")

    out = _amend(db_session, row.id, actor, fake_clock, status=None, note="家長來電", reason="補註")

    assert (out.status, out.note) == ("present", "家長來電")


def test_amend_leave_row_rejected(
    db_session: Session, actor: CurrentStaff, fake_clock: FakeClock
) -> None:
    student = make_student(db_session)
    leave = make_leave(db_session, student, start_date=_DAY)
    row = make_attendance(db_session, student, service_date=_DAY, status="leave", leave=leave)

    with pytest.raises(AppError) as managed:
        _amend(
            db_session,
            row.id,
            actor,
            fake_clock,
            status="present",
            check_in_at=_taipei(_DAY, 15),
            reason="x",
        )
    assert _error(managed) == (409, "attendance_managed_by_leave")

    with pytest.raises(AppError) as missing:
        _amend(db_session, uuid4(), actor, fake_clock, status="expected", reason="x")
    assert _error(missing) == (404, "attendance_not_found")


@pytest.mark.parametrize(
    "check_in_at",
    [
        _taipei(date(2026, 9, 2), 15),
        _taipei(date(2026, 8, 31), 23, 59),
        datetime.fromisoformat("9999-12-31T23:00:00-08:00"),
        datetime.fromisoformat("0001-01-01T00:00:00+08:00"),
    ],
)
def test_amend_time_wrong_date(
    db_session: Session, actor: CurrentStaff, fake_clock: FakeClock, check_in_at: datetime
) -> None:
    student = make_student(db_session)
    row = make_attendance(db_session, student, service_date=_DAY)

    with pytest.raises(AppError) as wrong:
        _amend(
            db_session,
            row.id,
            actor,
            fake_clock,
            status="present",
            check_in_at=check_in_at,
            reason="補登",
        )
    assert _error(wrong) == (422, "time_not_on_service_date")


def test_amend_time_utc_input_on_service_date(
    db_session: Session, actor: CurrentStaff, fake_clock: FakeClock
) -> None:
    """跨午夜：UTC 9/1 16:30 = 台北 9/2 00:30 → 拒絕；UTC 8/31 16:30 = 台北 9/1 00:30 → 可以。"""
    student = make_student(db_session)
    row = make_attendance(db_session, student, service_date=_DAY)

    with pytest.raises(AppError) as wrong:
        _amend(
            db_session,
            row.id,
            actor,
            fake_clock,
            status="present",
            check_in_at=datetime.fromisoformat("2026-09-01T16:30:00+00:00"),
            reason="補登",
        )
    assert _error(wrong) == (422, "time_not_on_service_date")

    out = _amend(
        db_session,
        row.id,
        actor,
        fake_clock,
        status="present",
        check_in_at=datetime.fromisoformat("2026-08-31T16:30:00+00:00"),
        reason="補登",
    )
    assert out.check_in_at == _taipei(_DAY, 0, 30)


def test_amend_no_changes(db_session: Session, actor: CurrentStaff, fake_clock: FakeClock) -> None:
    student = make_student(db_session)
    row = make_attendance(db_session, student, service_date=_DAY, status="present")

    with pytest.raises(AppError) as same_status:
        _amend(db_session, row.id, actor, fake_clock, status="present", reason="x")
    assert _error(same_status) == (422, "no_changes")

    with pytest.raises(AppError) as same_time:
        _amend(db_session, row.id, actor, fake_clock, check_in_at=_taipei(_DAY, 15), reason="x")
    assert _error(same_time) == (422, "no_changes")
    assert _amend_logs(db_session, row.id) == []


def test_amend_rollback_atomic(
    db_session: Session, actor: CurrentStaff, fake_clock: FakeClock
) -> None:
    student = make_student(db_session)
    row = make_attendance(db_session, student, service_date=_DAY, status="present")
    db_session.commit()

    _amend(db_session, row.id, actor, fake_clock, status="absent", reason="誤刷")
    assert len(_amend_logs(db_session, row.id)) == 1
    db_session.rollback()

    status = db_session.execute(
        select(StudentAttendance.status).where(StudentAttendance.id == row.id)
    ).scalar_one()
    assert status == "present"
    assert _amend_logs(db_session, row.id) == []


def test_amend_parent_ws_payload(
    db_session: Session, actor: CurrentStaff, fake_clock: FakeClock, published: list[Call]
) -> None:
    student = make_student(db_session, name="王小明")
    row = make_attendance(db_session, student, service_date=_DAY, status="present", note="內部備註")

    out = _amend(db_session, row.id, actor, fake_clock, status="expected", reason="誤刷")
    assert published == []
    db_session.commit()

    by_channel = {channels[0]: message for channels, message in published}
    assert set(by_channel) == {admin_topic_channel("attendance"), student_channel(student.id)}
    parent = by_channel[student_channel(student.id)]
    assert parent["type"] == "attendance.updated"
    assert parent["data"] == {
        "student_id": str(student.id),
        "service_date": "2026-09-01",
        "status": "expected",
        "check_in_at": None,
        "check_out_at": None,
    }
    admin = by_channel[admin_topic_channel("attendance")]
    assert admin["type"] == "attendance.updated"
    assert admin["data"]["id"] == str(row.id)
    assert admin["data"]["student_name"] == "王小明"
    assert admin["data"]["note"] == "內部備註"
    assert admin["data"]["status"] == out.status == "expected"
