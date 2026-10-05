"""attendance_service（domain_spec M4）。

- BACKEND-302：``ensure_attendance_row``（取得或建立出勤列，冪等、並發安全）。
- BACKEND-310：``amend_attendance``（改判，寫 audit、commit 後推播）。
- BACKEND-311：``get_daily_attendance``（每日清單、虛擬列與統計）。
- BACKEND-312：``get_monthly_attendance``（月出勤報表）。

營業時段讀 seed 預設（週一到週五營業、週六不營業、週日不列）；每個測試前後清空設定快取。
"""

import threading
from collections.abc import Iterator
from datetime import date, datetime, time
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, event, func, select
from sqlalchemy.orm import Session

from app.api.deps import CurrentStaff
from app.core.clock import combine_taipei
from app.core.errors import AppError
from app.core.request_meta import RequestMeta
from app.core.tx_hooks import install_tx_hooks
from app.models.account import StaffUser
from app.models.attendance import StudentAttendance
from app.models.audit import AuditLog
from app.models.reference import ClosedDay
from app.realtime import publish as publish_module
from app.realtime.publish import admin_topic_channel, student_channel
from app.schemas.attendance import (
    AttendanceAmendIn,
    DailyAttendanceQuery,
    MonthlyAttendanceQuery,
    MonthlyStudentRowOut,
)
from app.services.attendance_service import (
    amend_attendance,
    ensure_attendance_row,
    get_daily_attendance,
    get_monthly_attendance,
)
from app.services.settings_service import clear_settings_cache
from tests.integration.db.conftest import connect_owner
from tests.support.factories import (
    make_attendance,
    make_class,
    make_leave,
    make_staff,
    make_student,
)
from tests.support.fake_clock import FakeClock

_DAY = date(2026, 9, 1)
_META = RequestMeta(ip="203.0.113.5", user_agent="pytest", request_id=None)

Call = tuple[list[str], dict[str, Any]]


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    clear_settings_cache()
    yield
    clear_settings_cache()


class _SqlCounter:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def __call__(self, conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        self.statements.append(statement)


@pytest.fixture
def count_sql(db_session: Session) -> Iterator[_SqlCounter]:
    counter = _SqlCounter()
    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", counter)
    try:
        yield counter
    finally:
        event.remove(engine, "before_cursor_execute", counter)


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


# --- BACKEND-311 get_daily_attendance ---


def test_daily_attendance_rows_and_summary(db_session: Session, fake_clock: FakeClock) -> None:
    class_a = make_class(db_session, name="A班")
    ming = make_student(db_session, name="王小明", student_no="A001", class_=class_a)
    hua = make_student(db_session, name="陳小華", student_no="A002", class_=class_a)
    an = make_student(db_session, name="林小安", student_no="A003", class_=class_a)
    present = make_attendance(db_session, ming, service_date=_DAY, status="present")
    leave = make_leave(db_session, hua, start_date=_DAY, end_date=date(2026, 9, 2))
    make_attendance(db_session, hua, service_date=_DAY, status="leave", leave=leave)
    # 別天的列不影響
    make_attendance(db_session, an, service_date=date(2026, 9, 2), status="absent")

    out = get_daily_attendance(
        db_session, DailyAttendanceQuery(date=_DAY, class_id=class_a.id), clock=fake_clock
    )

    assert (out.date, out.is_service_day) == (_DAY, True)
    assert [(r.student_name, r.status) for r in out.items] == [
        ("王小明", "present"),
        ("陳小華", "leave"),
        ("林小安", "expected"),
    ]
    ming_row, hua_row, an_row = out.items
    assert ming_row.id == present.id
    assert ming_row.check_in_at == _taipei(_DAY, 15)
    assert (ming_row.class_id, ming_row.class_name) == (class_a.id, "A班")
    assert ming_row.leave is None
    assert hua_row.leave is not None
    assert (hua_row.leave.id, hua_row.leave.leave_type) == (leave.id, "sick")
    assert hua_row.leave.leave_type_label == "病假"
    assert (an_row.id, an_row.status, an_row.updated_at) == (None, "expected", None)
    assert (an_row.student_no, an_row.class_name) == ("A003", "A班")
    assert out.summary.model_dump() == {
        "total": 3,
        "expected": 1,
        "present": 1,
        "left": 0,
        "absent": 0,
        "leave": 1,
    }


def test_daily_attendance_defaults_to_today(db_session: Session, fake_clock: FakeClock) -> None:
    """query.date 未給 → clock.today()（台北）；UTC 9/1 16:30 已是台北 9/2。"""
    class_a = make_class(db_session)
    make_student(db_session, class_=class_a)
    fake_clock.set(datetime.fromisoformat("2026-09-01T16:30:00+00:00"))

    out = get_daily_attendance(
        db_session, DailyAttendanceQuery(class_id=class_a.id), clock=fake_clock
    )

    assert out.date == date(2026, 9, 2)
    assert [r.service_date for r in out.items] == [date(2026, 9, 2)]


def test_daily_attendance_filters(db_session: Session, fake_clock: FakeClock) -> None:
    class_a = make_class(db_session, name="A班")
    class_b = make_class(db_session, name="B班")
    ming = make_student(db_session, name="王小明", class_=class_a)
    hua = make_student(db_session, name="陳小華", class_=class_a)
    make_student(db_session, name="林小安", class_=class_a)
    mei = make_student(db_session, name="張小美", class_=class_b)
    make_attendance(db_session, ming, service_date=_DAY, status="present")
    make_attendance(db_session, hua, service_date=_DAY, status="absent")
    make_attendance(db_session, mei, service_date=_DAY, status="present")

    only_b = get_daily_attendance(
        db_session, DailyAttendanceQuery(date=_DAY, class_id=class_b.id), clock=fake_clock
    )
    present_a = get_daily_attendance(
        db_session,
        DailyAttendanceQuery(date=_DAY, class_id=class_a.id, status="present"),
        clock=fake_clock,
    )
    expected_a = get_daily_attendance(
        db_session,
        DailyAttendanceQuery(date=_DAY, class_id=class_a.id, status="expected"),
        clock=fake_clock,
    )

    assert [r.student_name for r in only_b.items] == ["張小美"]
    assert [r.student_name for r in present_a.items] == ["王小明"]
    assert present_a.summary.total == 3
    assert (present_a.summary.present, present_a.summary.absent) == (1, 1)
    # 虛擬列也套用 status 篩選
    assert [(r.student_name, r.id) for r in expected_a.items] == [("林小安", None)]


def test_daily_attendance_sorted_by_class_then_student_no(
    db_session: Session, fake_clock: FakeClock
) -> None:
    late = make_class(db_session, name="甲班")
    early = make_class(db_session, name="乙班")
    early.sort_order = -1
    db_session.flush()
    make_student(db_session, name="王小明", student_no="Z-002", class_=late)
    make_student(db_session, name="陳小華", student_no="Z-001", class_=late)
    make_student(db_session, name="林小安", student_no="Z-003", class_=early)
    mine = {"王小明", "陳小華", "林小安"}

    out = get_daily_attendance(db_session, DailyAttendanceQuery(date=_DAY), clock=fake_clock)

    assert [r.student_name for r in out.items if r.student_name in mine] == [
        "林小安",
        "陳小華",
        "王小明",
    ]


def test_daily_attendance_non_service_day(db_session: Session, fake_clock: FakeClock) -> None:
    class_a = make_class(db_session)
    make_student(db_session, class_=class_a)
    sunday = date(2026, 9, 6)

    out = get_daily_attendance(
        db_session, DailyAttendanceQuery(date=sunday, class_id=class_a.id), clock=fake_clock
    )

    assert out.is_service_day is False
    assert out.items == []
    assert out.summary.total == 0


def test_daily_attendance_non_service_day_keeps_actual_rows(
    db_session: Session, fake_clock: FakeClock
) -> None:
    class_a = make_class(db_session)
    ming = make_student(db_session, name="王小明", class_=class_a)
    make_student(db_session, name="陳小華", class_=class_a)
    saturday = date(2026, 9, 5)
    make_attendance(db_session, ming, service_date=saturday, status="present")

    out = get_daily_attendance(
        db_session, DailyAttendanceQuery(date=saturday, class_id=class_a.id), clock=fake_clock
    )

    assert out.is_service_day is False
    assert [(r.student_name, r.status) for r in out.items] == [("王小明", "present")]


def test_daily_attendance_withdrawn_with_row(db_session: Session, fake_clock: FakeClock) -> None:
    class_a = make_class(db_session)
    mei = make_student(db_session, name="張小美", class_=class_a)
    make_attendance(db_session, mei, service_date=_DAY, status="left")
    mei.status = "withdrawn"
    mei.withdrawn_on = _DAY
    # 已退班且當天沒有列的學生不出現
    gone = make_student(db_session, name="林小安", class_=class_a)
    gone.status = "withdrawn"
    gone.withdrawn_on = _DAY
    archived = make_student(db_session, name="陳小華", class_=class_a, archived=True)
    db_session.flush()

    out = get_daily_attendance(
        db_session, DailyAttendanceQuery(date=_DAY, class_id=class_a.id), clock=fake_clock
    )

    assert [(r.student_name, r.status) for r in out.items] == [("張小美", "left")]
    assert archived.id not in {r.student_id for r in out.items}


def test_daily_attendance_query_count(
    db_session: Session, fake_clock: FakeClock, count_sql: _SqlCounter
) -> None:
    class_a = make_class(db_session)
    for index in range(30):
        student = make_student(db_session, name=f"學生{index}", class_=class_a)
        if index % 3 == 0:
            leave = make_leave(db_session, student, start_date=_DAY)
            make_attendance(db_session, student, service_date=_DAY, status="leave", leave=leave)
        elif index % 3 == 1:
            make_attendance(db_session, student, service_date=_DAY, status="present")
    count_sql.statements.clear()

    out = get_daily_attendance(
        db_session, DailyAttendanceQuery(date=_DAY, class_id=class_a.id), clock=fake_clock
    )

    assert len(out.items) == 30
    assert out.summary.model_dump() == {
        "total": 30,
        "expected": 10,
        "present": 10,
        "left": 0,
        "absent": 0,
        "leave": 10,
    }
    assert len(count_sql.statements) <= 4


# --- BACKEND-312 get_monthly_attendance ---


def _closed(session: Session, d: date) -> None:
    session.add(ClosedDay(date=d, reason="颱風假"))
    session.flush()


def _monthly_row(rows: list[MonthlyStudentRowOut], student_id: UUID) -> MonthlyStudentRowOut:
    [row] = [r for r in rows if r.student_id == student_id]
    return row


@pytest.mark.clock("2026-09-10T15:00:00+08:00")
def test_monthly_attendance_days(db_session: Session, fake_clock: FakeClock) -> None:
    class_a = make_class(db_session, name="A班")
    _closed(db_session, date(2026, 9, 3))

    out = get_monthly_attendance(
        db_session, MonthlyAttendanceQuery(month="2026-09", class_id=class_a.id), clock=fake_clock
    )

    assert (out.month, out.class_id, out.class_name) == ("2026-09", class_a.id, "A班")
    assert len(out.days) == 30
    assert out.days[0].model_dump() == {
        "date": date(2026, 9, 1),
        "weekday": 1,
        "is_service_day": True,
    }
    assert out.days[2].is_service_day is False  # 9/3 休息日
    assert out.days[4].is_service_day is False  # 9/5 週六（seed 不營業）
    assert out.days[5].is_service_day is False  # 9/6 週日
    assert out.days[29].date == date(2026, 9, 30)
    assert out.students == []


@pytest.mark.clock("2026-09-10T15:00:00+08:00")
def test_monthly_attendance_stats(db_session: Session, fake_clock: FakeClock) -> None:
    class_a = make_class(db_session, name="A班")
    _closed(db_session, date(2026, 9, 3))
    ming = make_student(db_session, name="王小明", class_=class_a)
    make_attendance(db_session, ming, service_date=date(2026, 9, 1), status="present")
    make_attendance(db_session, ming, service_date=date(2026, 9, 2), status="left")
    make_attendance(db_session, ming, service_date=date(2026, 9, 4), status="absent")
    leave = make_leave(db_session, ming, start_date=date(2026, 9, 7))
    make_attendance(db_session, ming, service_date=date(2026, 9, 7), status="leave", leave=leave)
    make_attendance(db_session, ming, service_date=date(2026, 9, 8), status="expected")
    # 未來日期（9/11 之後）的列與非營業日的列都不計
    make_attendance(db_session, ming, service_date=date(2026, 9, 14), status="absent")
    make_attendance(db_session, ming, service_date=date(2026, 9, 5), status="present")

    out = get_monthly_attendance(
        db_session, MonthlyAttendanceQuery(month="2026-09", class_id=class_a.id), clock=fake_clock
    )

    row = _monthly_row(out.students, ming.id)
    assert (row.name, row.class_name) == ("王小明", "A班")
    assert row.stats.model_dump() == {
        "service_days": 7,
        "attended": 2,
        "absent": 1,
        "leave": 1,
        "unrecorded": 3,
    }
    assert len(row.statuses) == 30
    assert row.statuses[0] == "present"
    assert row.statuses[1] == "left"
    assert row.statuses[2] is None  # 9/3 休息日
    assert row.statuses[4] is None  # 9/5 週六：即使有列也不顯示
    assert row.statuses[6] == "leave"
    assert row.statuses[8] is None  # 9/9 無列
    assert row.statuses[13] == "absent"  # 9/14 未來日期照實顯示，只是不計入
    assert out.totals == row.stats


@pytest.mark.clock("2026-09-10T15:00:00+08:00")
def test_monthly_attendance_enrolled_mid_month(db_session: Session, fake_clock: FakeClock) -> None:
    class_a = make_class(db_session)
    ming = make_student(db_session, name="王小明", student_no="M-001", class_=class_a)
    hua = make_student(db_session, name="陳小華", student_no="M-002", class_=class_a)
    hua.enrolled_on = date(2026, 9, 8)
    db_session.flush()
    make_attendance(db_session, hua, service_date=date(2026, 9, 8), status="present")

    out = get_monthly_attendance(
        db_session, MonthlyAttendanceQuery(month="2026-09", class_id=class_a.id), clock=fake_clock
    )

    assert [r.name for r in out.students] == ["王小明", "陳小華"]
    hua_row = _monthly_row(out.students, hua.id)
    assert hua_row.stats.model_dump() == {
        "service_days": 3,
        "attended": 1,
        "absent": 0,
        "leave": 0,
        "unrecorded": 2,
    }
    ming_row = _monthly_row(out.students, ming.id)
    # 9/1~9/10 的營業日（9/5、9/6 週末）共 8 天，都沒有列
    assert ming_row.stats.service_days == 8
    assert ming_row.stats.unrecorded == 8
    assert out.totals.model_dump() == {
        "service_days": 11,
        "attended": 1,
        "absent": 0,
        "leave": 0,
        "unrecorded": 10,
    }


@pytest.mark.clock("2026-09-10T15:00:00+08:00")
def test_monthly_attendance_past_month_counts_all_days(
    db_session: Session, fake_clock: FakeClock
) -> None:
    class_a = make_class(db_session)
    ming = make_student(db_session, class_=class_a)
    make_attendance(db_session, ming, service_date=date(2026, 8, 31), status="present")

    out = get_monthly_attendance(
        db_session, MonthlyAttendanceQuery(month="2026-08", class_id=class_a.id), clock=fake_clock
    )

    assert len(out.days) == 31
    row = _monthly_row(out.students, ming.id)
    # 2026-08 週一到週五共 21 天
    assert (row.stats.service_days, row.stats.attended, row.stats.unrecorded) == (21, 1, 20)


@pytest.mark.clock("2026-09-10T15:00:00+08:00")
def test_monthly_attendance_class_filter_and_query_count(
    db_session: Session, fake_clock: FakeClock, count_sql: _SqlCounter
) -> None:
    class_a = make_class(db_session, name="A班")
    class_b = make_class(db_session, name="B班")
    ids = []
    for index in range(30):
        student = make_student(db_session, name=f"學生{index}", class_=class_a)
        ids.append(student.id)
        make_attendance(db_session, student, service_date=date(2026, 9, 1), status="present")
        make_attendance(db_session, student, service_date=date(2026, 9, 2), status="absent")
    other = make_student(db_session, name="陳小華", class_=class_b)
    # 已退班但本月有列的學生仍列出
    gone = make_student(db_session, name="張小美", class_=class_a)
    make_attendance(db_session, gone, service_date=date(2026, 9, 1), status="left")
    gone.status = "withdrawn"
    gone.withdrawn_on = date(2026, 9, 2)
    db_session.flush()
    count_sql.statements.clear()

    out = get_monthly_attendance(
        db_session, MonthlyAttendanceQuery(month="2026-09", class_id=class_a.id), clock=fake_clock
    )

    student_ids = {r.student_id for r in out.students}
    assert student_ids == {*ids, gone.id}
    assert other.id not in student_ids
    assert out.totals.attended == 31
    assert out.totals.absent == 30
    assert len(count_sql.statements) <= 4


@pytest.mark.parametrize("month", ["0000-01"])
def test_monthly_attendance_invalid_month(
    db_session: Session, fake_clock: FakeClock, month: str
) -> None:
    with pytest.raises(AppError) as invalid:
        get_monthly_attendance(db_session, MonthlyAttendanceQuery(month=month), clock=fake_clock)
    assert _error(invalid) == (422, "invalid_month")


# schema 的 \d 會接受全形數字（FULLWIDTH DIGIT）的年份；月份是 0[1-9]|1[0-2] 只收半形
_FULLWIDTH_2026_09 = "\uff12\uff10\uff12\uff16-09"


def test_monthly_attendance_fullwidth_month_normalized(
    db_session: Session, fake_clock: FakeClock
) -> None:
    class_a = make_class(db_session)

    out = get_monthly_attendance(
        db_session,
        MonthlyAttendanceQuery(month=_FULLWIDTH_2026_09, class_id=class_a.id),
        clock=fake_clock,
    )

    assert out.month == "2026-09"
    assert len(out.days) == 30
