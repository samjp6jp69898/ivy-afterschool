"""app/services/pickup/requests.py（接送請求查詢與回覆同步）。

- BACKEND-414：get_queue（接送佇列）。
- BACKEND-415：get_roster（接送 POS 學生卡）。
- BACKEND-416：list_today_requests_for_parent（家長端今日請求）。
- BACKEND-413：sync_open_request_reply（作業完成或 ETA 變動時同步回覆）。

營運資料以 db_session 建立、測試結束 rollback；計數類斷言以班級或本測試建立的 id 限縮範圍。
"""

from collections.abc import Iterator
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.orm import Session

from app.core.clock import combine_taipei
from app.core.config import get_settings
from app.core.crypto import derive_key
from app.core.tx_hooks import install_tx_hooks
from app.models.homework import HomeworkDailyProgress
from app.models.notifications import Notification
from app.models.pickup import PickupRequest
from app.realtime import publish as publish_module
from app.realtime.publish import admin_topic_channel
from app.schemas.pickup import PickupQueueQuery, RosterQuery
from app.services.pickup.auto_reply import DONE_REPLY_TEXT
from app.services.pickup.requests import (
    get_queue,
    get_roster,
    list_today_requests_for_parent,
    sync_open_request_reply,
)
from app.services.settings_service import clear_settings_cache
from tests.support.factories import (
    ARCHIVED_AT,
    make_attendance,
    make_class,
    make_guardian,
    make_homework_progress,
    make_leave,
    make_parent,
    make_pickup_authorization,
    make_pickup_request,
    make_staff,
    make_student,
)
from tests.support.fake_clock import FakeClock

_TODAY = date(2026, 9, 10)  # 週四
_CLOCK_NOW = datetime(2026, 9, 10, 8, 0, tzinfo=UTC)  # 台北 16:00
Call = tuple[list[str], dict[str, Any]]


@pytest.fixture(autouse=True)
def _crypto_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """make_pickup_authorization 以 HMAC 計算 code_hash，需要 APP_SECRET_KEY。"""
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


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(_CLOCK_NOW)


@pytest.fixture
def published(monkeypatch: pytest.MonkeyPatch) -> list[Call]:
    install_tx_hooks()
    calls: list[Call] = []

    def record(channels: list[str], message: dict[str, Any]) -> None:
        calls.append((list(channels), dict(message)))

    monkeypatch.setattr(publish_module, "publish_threadsafe", record)
    return calls


def _arrival(hour: int, minute: int = 0) -> datetime:
    return combine_taipei(_TODAY, time(hour, minute))


# --- BACKEND-414 get_queue ---


def _queue_fixture(db: Session) -> dict[str, PickupRequest]:
    students = [make_student(db, name=f"佇列{i}") for i in range(6)]
    pending_eta = make_pickup_request(
        db, students[0], service_date=_TODAY, expected_arrival_at=_arrival(17, 30)
    )
    arrived = make_pickup_request(
        db, students[1], service_date=_TODAY, status="arrived", reply_source="staff"
    )
    acknowledged = make_pickup_request(
        db, students[2], service_date=_TODAY, status="acknowledged", reply_source="staff"
    )
    pending_no_eta = make_pickup_request(db, students[3], service_date=_TODAY)
    completed = make_pickup_request(db, students[4], service_date=_TODAY, status="completed")
    # 別天的請求不出現
    yesterday = make_pickup_request(db, students[5], service_date=_TODAY - timedelta(days=1))
    return {
        "pending_eta": pending_eta,
        "arrived": arrived,
        "acknowledged": acknowledged,
        "pending_no_eta": pending_no_eta,
        "completed": completed,
        "yesterday": yesterday,
    }


def test_pickup_queue_order(db_session: Session, clock: FakeClock) -> None:
    r = _queue_fixture(db_session)
    # 同為 pending、ETA 相同時依 created_at
    later = make_pickup_request(
        db_session,
        make_student(db_session),
        service_date=_TODAY,
        expected_arrival_at=_arrival(17, 30),
    )
    later.created_at = r["pending_eta"].created_at + timedelta(minutes=1)
    db_session.flush()

    queue = get_queue(db_session, PickupQueueQuery(), clock=clock)

    assert queue.date == _TODAY
    assert [o.id for o in queue.open] == [
        r["arrived"].id,
        r["pending_eta"].id,
        later.id,
        r["pending_no_eta"].id,
        r["acknowledged"].id,
    ]
    assert [o.status for o in queue.open] == [
        "arrived",
        "pending",
        "pending",
        "pending",
        "acknowledged",
    ]
    assert queue.open[1].expected_arrival_at == "17:30"
    assert [c.id for c in queue.closed] == [r["completed"].id]


def test_pickup_queue_closed_newest_first(db_session: Session, clock: FakeClock) -> None:
    ming, hua, an = (make_student(db_session) for _ in range(3))
    done = make_pickup_request(db_session, ming, service_date=_TODAY, status="completed")
    cancelled = make_pickup_request(db_session, hua, service_date=_TODAY, status="cancelled")
    expired = make_pickup_request(db_session, an, service_date=_TODAY, status="expired")
    done.completed_at = datetime(2026, 9, 10, 9, 0, tzinfo=UTC)
    cancelled.cancelled_at = datetime(2026, 9, 10, 10, 0, tzinfo=UTC)
    db_session.flush()

    queue = get_queue(db_session, PickupQueueQuery(), clock=clock)

    # expired 沒有完成 / 取消時間，以 updated_at（測試交易內為 now()，晚於 2026-09-10）排序
    assert [c.id for c in queue.closed] == [expired.id, cancelled.id, done.id]


def test_pickup_queue_counts(db_session: Session, clock: FakeClock) -> None:
    _queue_fixture(db_session)

    queue = get_queue(db_session, PickupQueueQuery(), clock=clock)

    assert queue.counts.model_dump() == {
        "pending": 2,
        "acknowledged": 1,
        "arrived": 1,
        "needs_reply": 2,
    }


def test_pickup_queue_other_date(db_session: Session, clock: FakeClock) -> None:
    r = _queue_fixture(db_session)

    queue = get_queue(db_session, PickupQueueQuery(date=_TODAY - timedelta(days=1)), clock=clock)

    assert queue.date == _TODAY - timedelta(days=1)
    assert [o.id for o in queue.open] == [r["yesterday"].id]
    assert queue.closed == []
    assert queue.counts.pending == 1


# --- BACKEND-415 get_roster ---


def test_pickup_roster_groups(db_session: Session, clock: FakeClock) -> None:
    late = make_class(db_session, name="乙班")
    class_a = make_class(db_session, name="甲班")
    class_a.sort_order = -1
    db_session.flush()
    make_student(db_session, name="王小明", student_no="R-002", class_=class_a)
    make_student(db_session, name="陳小華", student_no="R-001", class_=class_a)
    make_student(db_session, name="張小美", class_=late)
    an = make_student(db_session, name="林小安")
    gone = make_student(db_session, name="李小東", class_=class_a)
    gone.status = "withdrawn"
    gone.withdrawn_on = date(2026, 9, 1)
    make_student(db_session, name="周小西", class_=class_a, archived=True)
    db_session.flush()

    roster = get_roster(db_session, RosterQuery(), clock=clock)

    groups = [
        g for g in roster.classes if g.class_id in {class_a.id, late.id} or g.class_id is None
    ]
    assert [g.class_name for g in groups] == ["甲班", "乙班", "未分班"]
    assert [s.name for s in groups[0].students] == ["陳小華", "王小明"]
    assert roster.classes[-1].class_id is None
    assert an.id in {s.student_id for s in roster.classes[-1].students}
    assert roster.date == _TODAY


def test_pickup_roster_class_filter(db_session: Session, clock: FakeClock) -> None:
    class_a = make_class(db_session, name="甲班")
    make_student(db_session, name="王小明", class_=class_a)
    make_student(db_session, name="林小安")

    roster = get_roster(db_session, RosterQuery(class_id=class_a.id), clock=clock)

    assert [(g.class_name, [s.name for s in g.students]) for g in roster.classes] == [
        ("甲班", ["王小明"])
    ]


def test_pickup_roster_fields(db_session: Session, clock: FakeClock) -> None:
    class_a = make_class(db_session, name="甲班")
    ming = make_student(db_session, name="王小明", student_no="F-001", class_=class_a)
    hua = make_student(db_session, name="陳小華", student_no="F-002", class_=class_a)
    an = make_student(db_session, name="林小安", student_no="F-003", class_=class_a)
    present = make_attendance(db_session, ming, service_date=_TODAY, status="present")
    make_homework_progress(
        db_session, ming, service_date=_TODAY, overall_status="in_progress", ready_eta=time(17, 30)
    )
    request = make_pickup_request(
        db_session, ming, service_date=_TODAY, expected_arrival_at=_arrival(17, 45)
    )
    make_pickup_authorization(db_session, ming, service_date=_TODAY)
    make_pickup_authorization(db_session, ming, service_date=_TODAY, status="cancelled")
    make_pickup_authorization(db_session, ming, service_date=_TODAY + timedelta(days=1))
    leave = make_leave(db_session, hua, start_date=_TODAY, leave_type="personal")
    make_attendance(db_session, hua, service_date=_TODAY, status="leave", leave=leave)
    # 終態請求不算 open_request
    make_pickup_request(db_session, hua, service_date=_TODAY, status="completed")

    roster = get_roster(db_session, RosterQuery(class_id=class_a.id), clock=clock)

    ming_card, hua_card, an_card = roster.classes[0].students
    assert (ming_card.student_id, ming_card.student_no, ming_card.grade_level) == (
        ming.id,
        "F-001",
        3,
    )
    assert ming_card.attendance_status == "present"
    assert ming_card.check_in_at == present.check_in_at
    assert ming_card.check_out_at is None
    assert (ming_card.homework_status, ming_card.ready_eta) == ("in_progress", "17:30")
    assert ming_card.open_request is not None
    assert ming_card.open_request.model_dump() == {
        "id": request.id,
        "status": "pending",
        "expected_arrival_at": "17:45",
        "needs_reply": True,
    }
    assert ming_card.active_authorization_count == 1
    assert (hua_card.attendance_status, hua_card.leave_type) == ("leave", "personal")
    assert hua_card.open_request is None
    assert an_card.student_id == an.id
    assert (an_card.attendance_status, an_card.leave_type, an_card.homework_status) == (
        None,
        None,
        None,
    )
    assert (an_card.open_request, an_card.active_authorization_count) == (None, 0)


def test_pickup_roster_query_count(db_session: Session, clock: FakeClock) -> None:
    class_a = make_class(db_session)
    for index in range(30):
        student = make_student(db_session, class_=class_a)
        if index % 3 == 0:
            leave = make_leave(db_session, student, start_date=_TODAY)
            make_attendance(db_session, student, service_date=_TODAY, status="leave", leave=leave)
        else:
            make_attendance(db_session, student, service_date=_TODAY, status="present")
        make_homework_progress(db_session, student, service_date=_TODAY)
        if index % 2 == 0:
            make_pickup_request(db_session, student, service_date=_TODAY)
        if index % 5 == 0:
            make_pickup_authorization(db_session, student, service_date=_TODAY)
    statements: list[str] = []

    def record(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        statements.append(statement)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", record)
    try:
        roster = get_roster(db_session, RosterQuery(class_id=class_a.id), clock=clock)
    finally:
        event.remove(engine, "before_cursor_execute", record)

    cards = roster.classes[0].students
    assert len(cards) == 30
    assert sum(1 for c in cards if c.leave_type == "sick") == 10
    assert sum(1 for c in cards if c.open_request is not None) == 15
    assert sum(c.active_authorization_count for c in cards) == 6
    assert len(statements) <= 7


# --- BACKEND-416 list_today_requests_for_parent ---


def test_parent_today_requests(db_session: Session, clock: FakeClock) -> None:
    parent = make_parent(db_session)
    ming = make_student(db_session, name="王小明")
    hua = make_student(db_session, name="陳小華")
    other = make_student(db_session, name="林小安")
    make_guardian(db_session, ming, parent=parent)
    make_guardian(db_session, hua, parent=parent)
    today_ming = make_pickup_request(db_session, ming, service_date=_TODAY, requested_by=parent.id)
    today_hua = make_pickup_request(db_session, hua, service_date=_TODAY, status="completed")
    make_pickup_request(
        db_session, ming, service_date=_TODAY - timedelta(days=1), status="completed"
    )
    make_pickup_request(db_session, other, service_date=_TODAY)
    today_hua.created_at = datetime(2026, 9, 10, 7, 0, tzinfo=UTC)
    today_ming.created_at = datetime(2026, 9, 10, 7, 30, tzinfo=UTC)
    db_session.flush()

    rows = list_today_requests_for_parent(db_session, parent.id, clock=clock)

    assert [r.id for r in rows] == [today_ming.id, today_hua.id]  # 新到舊
    assert {r.student_name for r in rows} == {"王小明", "陳小華"}
    assert [(r.status, r.can_cancel) for r in rows] == [("pending", True), ("completed", False)]


def test_parent_today_requests_follows_taipei_today(db_session: Session) -> None:
    """UTC 9/9 16:30 = 台北 9/10 00:30：已是 9/10。"""
    parent = make_parent(db_session)
    ming = make_student(db_session)
    make_guardian(db_session, ming, parent=parent)
    request = make_pickup_request(db_session, ming, service_date=_TODAY)

    rows = list_today_requests_for_parent(
        db_session, parent.id, clock=FakeClock(datetime(2026, 9, 9, 16, 30, tzinfo=UTC))
    )

    assert [r.id for r in rows] == [request.id]


def test_parent_today_requests_no_staff_names(db_session: Session, clock: FakeClock) -> None:
    parent = make_parent(db_session)
    ming = make_student(db_session)
    make_guardian(db_session, ming, parent=parent)
    make_pickup_request(
        db_session, ming, service_date=_TODAY, status="acknowledged", reply_source="staff"
    )

    [row] = list_today_requests_for_parent(db_session, parent.id, clock=clock)

    dumped = row.model_dump()
    assert dumped["reply_source"] == "staff"
    assert "replied_by_name" not in dumped
    assert "completed_by_name" not in dumped


def test_parent_today_requests_without_children(db_session: Session, clock: FakeClock) -> None:
    assert list_today_requests_for_parent(db_session, make_parent(db_session).id, clock=clock) == []


# --- BACKEND-413 sync_open_request_reply ---


def _progress(
    db: Session, student_id: UUID, *, overall: str, eta: time | None = None, note: str | None = None
) -> None:
    """建立或更新當日進度（模擬作業模組已寫入的狀態）。"""
    row = db.execute(
        select(HomeworkDailyProgress).where(
            HomeworkDailyProgress.student_id == student_id,
            HomeworkDailyProgress.service_date == _TODAY,
        )
    ).scalar_one_or_none()
    if row is None:
        row = HomeworkDailyProgress(student_id=student_id, service_date=_TODAY)
        db.add(row)
    row.overall_status = overall  # type: ignore[assignment]
    row.ready_eta = eta
    row.eta_updated_at = ARCHIVED_AT if eta is not None else None
    row.note = note
    db.flush()


def _replied_count(db: Session) -> int:
    return db.execute(
        select(func.count()).select_from(Notification).where(Notification.event == "pickup.replied")
    ).scalar_one()


def test_sync_reply_done_overrides_staff(db_session: Session, clock: FakeClock) -> None:
    ming = make_student(db_session)
    request = make_pickup_request(
        db_session,
        ming,
        service_date=_TODAY,
        reply_source="staff",
        reply_message="再等十分鐘",
        reply_ready_eta=time(17, 0),
    )
    _progress(db_session, ming.id, overall="done")

    synced = sync_open_request_reply(db_session, ming.id, _TODAY, clock=clock)

    assert synced is not None
    assert synced.id == request.id
    assert (synced.reply_message, synced.reply_source) == (DONE_REPLY_TEXT, "auto")
    assert (synced.replied_by, synced.reply_ready_eta) == (None, None)
    assert synced.replied_at == _CLOCK_NOW
    assert synced.status == "pending"


def test_sync_reply_done_overrides_auto(db_session: Session, clock: FakeClock) -> None:
    ming = make_student(db_session)
    make_pickup_request(
        db_session,
        ming,
        service_date=_TODAY,
        status="acknowledged",
        reply_source="auto",
        reply_message="已通知老師，稍後回覆預計時間",
    )
    _progress(db_session, ming.id, overall="done")

    synced = sync_open_request_reply(db_session, ming.id, _TODAY, clock=clock)

    assert synced is not None
    assert (synced.reply_message, synced.reply_source) == (DONE_REPLY_TEXT, "auto")


def test_sync_reply_eta_updates_auto(
    db_session: Session, clock: FakeClock, published: list[Call]
) -> None:
    ming = make_student(db_session)
    make_pickup_request(
        db_session,
        ming,
        service_date=_TODAY,
        reply_source="auto",
        reply_message="已通知老師，稍後回覆預計時間",
    )
    _progress(db_session, ming.id, overall="in_progress", eta=time(17, 30), note="剩數學訂正")

    synced = sync_open_request_reply(db_session, ming.id, _TODAY, clock=clock)

    assert synced is not None
    assert synced.reply_ready_eta == time(17, 30)
    assert synced.reply_message is not None
    assert synced.reply_message.startswith("預計 17:30 可接送")
    assert (synced.reply_source, synced.replied_at) == ("auto", _CLOCK_NOW)
    db_session.commit()
    assert _replied_count(db_session) == 0
    [message] = [m for ch, m in published if ch == [admin_topic_channel("pickup")]]
    assert message["data"]["reply_ready_eta"] == "17:30"


def test_sync_reply_no_change_no_publish(
    db_session: Session, clock: FakeClock, published: list[Call]
) -> None:
    ming = make_student(db_session)
    request = make_pickup_request(
        db_session,
        ming,
        service_date=_TODAY,
        reply_source="auto",
        reply_message="預計 17:30 可接送",
        reply_ready_eta=time(17, 30),
    )
    _progress(db_session, ming.id, overall="in_progress", eta=time(17, 30))

    synced = sync_open_request_reply(db_session, ming.id, _TODAY, clock=clock)
    db_session.commit()

    assert synced is not None
    assert (synced.id, synced.replied_at) == (request.id, ARCHIVED_AT)
    assert published == []


def test_sync_reply_eta_cleared_without_auto_text(db_session: Session, clock: FakeClock) -> None:
    """ETA 被清除且設定不自動回覆 → 回覆整組清空（source 與 replied_at 一併為 null）。"""
    db_session.execute(
        text(
            "update public.system_settings "
            "set value = jsonb_set(value, '{auto_reply_without_eta}', 'false'::jsonb) "
            "where key = 'homework.defaults'"
        )
    )
    clear_settings_cache()
    ming = make_student(db_session)
    make_pickup_request(
        db_session,
        ming,
        service_date=_TODAY,
        reply_source="auto",
        reply_message="預計 17:30 可接送",
        reply_ready_eta=time(17, 30),
    )
    _progress(db_session, ming.id, overall="in_progress")

    synced = sync_open_request_reply(db_session, ming.id, _TODAY, clock=clock)

    assert synced is not None
    assert (synced.reply_source, synced.reply_message, synced.reply_ready_eta) == (
        None,
        None,
        None,
    )
    assert synced.replied_at is None


def test_sync_reply_keeps_staff_on_eta_change(
    db_session: Session, clock: FakeClock, published: list[Call]
) -> None:
    ming = make_student(db_session)
    teacher = make_staff(db_session, display_name="林老師")
    request = make_pickup_request(
        db_session,
        ming,
        service_date=_TODAY,
        status="acknowledged",
        reply_source="staff",
        reply_message="17:30 好",
        reply_ready_eta=time(17, 30),
    )
    request.replied_by = teacher.id
    db_session.flush()
    _progress(db_session, ming.id, overall="in_progress", eta=time(18, 0))

    synced = sync_open_request_reply(db_session, ming.id, _TODAY, clock=clock)
    db_session.commit()

    assert synced is not None
    assert (synced.reply_source, synced.reply_message, synced.reply_ready_eta) == (
        "staff",
        "17:30 好",
        time(17, 30),
    )
    assert synced.replied_by == teacher.id
    assert published == []


def test_sync_reply_no_open_request(db_session: Session, clock: FakeClock) -> None:
    ming = make_student(db_session)
    make_pickup_request(db_session, ming, service_date=_TODAY, status="completed")
    # 別天的進行中請求不算
    make_pickup_request(db_session, ming, service_date=_TODAY - timedelta(days=1))
    _progress(db_session, ming.id, overall="done")

    assert sync_open_request_reply(db_session, ming.id, _TODAY, clock=clock) is None
