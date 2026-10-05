"""BACKEND-525：parent_today_service.get_child_today（家長端小孩今日狀態聚合）。

營業時段讀 seed 預設（週一到週五營業）；FakeClock 預設 2026-09-01（週二）09:00 台北。
"""

from collections.abc import Iterator
from datetime import UTC, date, datetime, time
from typing import Any

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.core.clock import combine_taipei
from app.core.config import get_settings
from app.core.crypto import derive_key
from app.services.parent_today_service import get_child_today
from app.services.settings_service import clear_settings_cache
from tests.support.factories import (
    make_attendance,
    make_guardian,
    make_homework_item,
    make_homework_progress,
    make_leave,
    make_pickup_authorization,
    make_pickup_request,
    make_staff,
    make_student,
)
from tests.support.fake_clock import FakeClock

_DAY = date(2026, 9, 1)


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """make_pickup_authorization 以 HMAC 計算 code_hash，需要 APP_SECRET_KEY；設定快取前後清空。"""
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@127.0.0.1:54342/postgres")
    monkeypatch.setenv("APP_SECRET_KEY", "s" * 48)
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://127.0.0.1:5341")
    monkeypatch.setenv("R2_ENDPOINT_URL", "http://127.0.0.1:54344")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "afterschool")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "afterschool-local-secret")
    monkeypatch.setenv("R2_BUCKET", "afterschool-local")
    get_settings.cache_clear()
    derive_key.cache_clear()
    clear_settings_cache()
    yield
    get_settings.cache_clear()
    derive_key.cache_clear()
    clear_settings_cache()


def test_child_today_full(db_session: Session, fake_clock: FakeClock) -> None:
    ming = make_student(db_session)
    make_attendance(db_session, ming, service_date=_DAY, status="present", note="內部備註")
    make_homework_item(db_session, ming, service_date=_DAY, status="done")
    make_homework_item(db_session, ming, service_date=_DAY, status="done", title="國語生字")
    make_homework_item(db_session, ming, service_date=_DAY, status="doing", title="英語")
    make_homework_item(db_session, ming, service_date=date(2026, 9, 2), title="隔天的作業")
    make_homework_progress(
        db_session,
        ming,
        service_date=_DAY,
        overall_status="in_progress",
        ready_eta=time(17, 30),
        note="剩數學訂正",
    )
    request = make_pickup_request(
        db_session,
        ming,
        service_date=_DAY,
        reply_source="auto",
        reply_message="預計 17:30 可接送",
        reply_ready_eta=time(17, 30),
        expected_arrival_at=combine_taipei(_DAY, time(17, 45)),
    )

    out = get_child_today(db_session, ming.id, clock=fake_clock)

    assert (out.student_id, out.date, out.is_service_day) == (ming.id, _DAY, True)
    assert out.attendance.status == "present"
    assert out.attendance.check_in_at == combine_taipei(_DAY, time(15, 0))
    assert out.homework.model_dump() == {
        "item_count": 3,
        "done_count": 2,
        "overall_status": "in_progress",
        "ready_eta": "17:30",
        "note": "剩數學訂正",
    }
    assert out.pickup_request is not None
    assert out.pickup_request.model_dump() == {
        "id": request.id,
        "status": "pending",
        "expected_arrival_at": "17:45",
        "reply_ready_eta": "17:30",
        "reply_message": "預計 17:30 可接送",
        "reply_source": "auto",
        "completed_at": None,
        "picked_up_by_name": None,
        "can_cancel": True,
        "can_mark_arrived": True,
    }
    assert (out.on_leave, out.leave) == (False, None)
    assert "內部備註" not in out.model_dump_json()


def test_child_today_defaults(db_session: Session, fake_clock: FakeClock) -> None:
    ming = make_student(db_session)

    out = get_child_today(db_session, ming.id, clock=fake_clock)

    assert out.is_service_day is True
    assert out.attendance.model_dump() == {
        "status": "expected",
        "check_in_at": None,
        "check_out_at": None,
    }
    assert out.homework.model_dump() == {
        "item_count": 0,
        "done_count": 0,
        "overall_status": "not_started",
        "ready_eta": None,
        "note": None,
    }
    assert (out.pickup_request, out.on_leave, out.leave) == (None, False, None)

    fake_clock.set(datetime(2026, 9, 6, 2, 0, tzinfo=UTC))  # 週日
    sunday = get_child_today(db_session, ming.id, clock=fake_clock)

    assert (sunday.date, sunday.is_service_day) == (date(2026, 9, 6), False)
    assert sunday.attendance.status is None


def test_child_today_on_leave(db_session: Session, fake_clock: FakeClock) -> None:
    ming = make_student(db_session)
    leave = make_leave(
        db_session, ming, start_date=_DAY, end_date=date(2026, 9, 2), leave_type="personal"
    )
    make_attendance(db_session, ming, service_date=_DAY, status="leave", leave=leave)

    out = get_child_today(db_session, ming.id, clock=fake_clock)

    assert out.on_leave is True
    assert out.leave is not None
    assert out.leave.model_dump() == {
        "id": leave.id,
        "leave_type": "personal",
        "leave_type_label": "事假",
        "start_date": _DAY,
        "end_date": date(2026, 9, 2),
    }
    assert out.attendance.status == "leave"


def test_child_today_on_leave_without_row(db_session: Session, fake_clock: FakeClock) -> None:
    """on_leave 不依賴出勤列；已取消的請假不算。"""
    ming = make_student(db_session)
    leave = make_leave(db_session, ming, start_date=_DAY, end_date=date(2026, 9, 2))
    hua = make_student(db_session, name="陳小華")
    make_leave(db_session, hua, start_date=_DAY, status="cancelled")

    out = get_child_today(db_session, ming.id, clock=fake_clock)
    assert out.leave is not None
    assert (out.on_leave, out.leave.id, out.attendance.status) == (True, leave.id, "expected")
    assert get_child_today(db_session, hua.id, clock=fake_clock).on_leave is False


def test_child_today_pickup_pick(db_session: Session, fake_clock: FakeClock) -> None:
    ming = make_student(db_session)
    completed = make_pickup_request(db_session, ming, service_date=_DAY, status="completed")
    pending = make_pickup_request(db_session, ming, service_date=_DAY)
    completed.created_at = combine_taipei(_DAY, time(10, 0))
    pending.created_at = combine_taipei(_DAY, time(11, 0))
    db_session.flush()

    picked = get_child_today(db_session, ming.id, clock=fake_clock).pickup_request
    assert picked is not None
    assert picked.id == pending.id

    hua = make_student(db_session, name="陳小華")
    older = make_pickup_request(db_session, hua, service_date=_DAY, status="cancelled")
    newer = make_pickup_request(db_session, hua, service_date=_DAY, status="completed")
    older.created_at = combine_taipei(_DAY, time(10, 0))
    newer.created_at = combine_taipei(_DAY, time(11, 0))
    db_session.flush()

    latest = get_child_today(db_session, hua.id, clock=fake_clock).pickup_request
    assert latest is not None
    assert (latest.id, latest.can_cancel, latest.can_mark_arrived) == (newer.id, False, False)


def test_child_today_query_count(db_session: Session, fake_clock: FakeClock) -> None:
    ming = make_student(db_session)
    make_attendance(db_session, ming, service_date=_DAY, status="present")
    make_homework_item(db_session, ming, service_date=_DAY)
    make_homework_progress(db_session, ming, service_date=_DAY)
    make_leave(db_session, ming, start_date=date(2026, 9, 3))
    make_pickup_request(
        db_session, ming, service_date=_DAY, status="acknowledged", reply_source="staff"
    )
    statements: list[str] = []

    def record(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        statements.append(statement)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", record)
    try:
        out = get_child_today(db_session, ming.id, clock=fake_clock)
    finally:
        event.remove(engine, "before_cursor_execute", record)

    assert len(statements) <= 6
    assert out.pickup_request is not None
    assert out.pickup_request.reply_source == "staff"
    assert "replied_by_name" not in out.model_dump_json()
    assert "林老師" not in out.model_dump_json()


def test_child_today_completed_pickup(db_session: Session, fake_clock: FakeClock) -> None:
    ming = make_student(db_session)
    dad = make_guardian(db_session, ming, name="王爸爸", relation="father")
    by_guardian = make_pickup_request(db_session, ming, service_date=_DAY, status="completed")
    by_guardian.completion_method = "guardian"
    by_guardian.picked_up_by_guardian_id = dad.id
    by_guardian.completed_at = combine_taipei(_DAY, time(18, 5))
    by_guardian.completed_by = make_staff(db_session).id
    db_session.flush()

    out = get_child_today(db_session, ming.id, clock=fake_clock).pickup_request

    assert out is not None
    assert out.completed_at == datetime(2026, 9, 1, 10, 5, tzinfo=UTC)
    assert out.picked_up_by_name == "王爸爸"

    hua = make_student(db_session, name="陳小華")
    auth = make_pickup_authorization(db_session, hua, service_date=_DAY, proxy_name="李阿姨")
    by_proxy = make_pickup_request(db_session, hua, service_date=_DAY, status="completed")
    by_proxy.completion_method = "code"
    by_proxy.picked_up_by_authorization_id = auth.id
    db_session.flush()
    proxy_out = get_child_today(db_session, hua.id, clock=fake_clock).pickup_request
    assert proxy_out is not None
    assert proxy_out.picked_up_by_name == "李阿姨"

    an = make_student(db_session, name="林小安")
    make_pickup_request(db_session, an, service_date=_DAY, status="completed")  # override
    override_out = get_child_today(db_session, an.id, clock=fake_clock).pickup_request
    assert override_out is not None
    assert override_out.picked_up_by_name == "老師確認交付"

    li = make_student(db_session, name="李小東")
    make_pickup_request(db_session, li, service_date=_DAY, status="arrived", reply_source="staff")
    open_out = get_child_today(db_session, li.id, clock=fake_clock).pickup_request
    assert open_out is not None
    assert (open_out.completed_at, open_out.picked_up_by_name) == (None, None)
    assert (open_out.can_cancel, open_out.can_mark_arrived) == (True, False)
