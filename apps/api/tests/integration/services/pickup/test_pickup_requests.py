"""app/services/pickup/requests.py（接送請求查詢與回覆同步）。

- BACKEND-414：get_queue（接送佇列）。
- BACKEND-415：get_roster（接送 POS 學生卡）。
- BACKEND-416：list_today_requests_for_parent（家長端今日請求）。
- BACKEND-413：sync_open_request_reply（作業完成或 ETA 變動時同步回覆）。
- BACKEND-406 / 407 / 408 / 409 / 411：create_request、reply_request、acknowledge_request、
  mark_arrived、cancel_request（狀態轉換、通知、推播）。

營運資料以 db_session 建立、測試結束 rollback；計數類斷言以班級或本測試建立的 id 限縮範圍。
"""

import threading
from collections.abc import Iterator
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, event, func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.api.deps import CurrentParent, CurrentStaff
from app.core.clock import combine_taipei
from app.core.config import get_settings
from app.core.crypto import derive_key
from app.core.errors import AppError
from app.core.tx_hooks import install_tx_hooks
from app.models.account import StaffUser
from app.models.homework import HomeworkDailyProgress
from app.models.notifications import Notification, NotificationOutbox
from app.models.parents import ParentAccount
from app.models.pickup import PickupRequest
from app.models.reference import ClosedDay
from app.notifications import outbox_jobs
from app.realtime import publish as publish_module
from app.realtime.publish import admin_topic_channel, staff_channel
from app.schemas.pickup import (
    ParentPickupRequestCreateIn,
    PickupCancelIn,
    PickupQueueQuery,
    PickupReplyIn,
    RosterQuery,
    StaffPickupRequestCreateIn,
)
from app.services.audit_service import Actor
from app.services.pickup.auto_reply import DONE_REPLY_TEXT
from app.services.pickup.requests import (
    acknowledge_request,
    cancel_request,
    create_request,
    get_queue,
    get_roster,
    list_today_requests_for_parent,
    mark_arrived,
    reply_request,
    sync_open_request_reply,
)
from app.services.pickup.views import build_request_views
from app.services.settings_service import clear_settings_cache
from tests.integration.db.conftest import connect_owner
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


def _people_counts() -> tuple[int, int]:
    with connect_owner() as conn:
        staff = conn.execute("select count(*) from public.staff_users").fetchone()
        roles = conn.execute("select count(*) from public.roles where not is_system").fetchone()
    assert staff is not None
    assert roles is not None
    return staff[0], roles[0]


@pytest.fixture
def no_people_residue() -> Iterator[None]:
    """BACKEND-546：committing 測試結束（含 owner 清理）後 staff_users 與自訂角色筆數不變。

    放在參數第一個：最先建立、最後拆除，拆除時其他清理 fixture 都已執行完。
    """
    before = _people_counts()
    yield
    assert _people_counts() == before


@pytest.fixture
def owner_cleanup() -> Iterator[list[tuple[str, UUID]]]:
    """committing 測試建立的學生 / 員工以 owner 連線刪除；排在 committing_db_session 之前
    （先 close session、truncate 請求與進度表，再刪人）。員工一律用 seed 系統角色，不建自訂角色。"""
    rows: list[tuple[str, UUID]] = []
    yield rows
    with connect_owner() as conn:
        conn.execute("set lock_timeout = '5s'")
        for table, row_id in rows:
            conn.execute(f"delete from public.{table} where id = %s", (row_id,))  # noqa: S608
        conn.commit()


@pytest.mark.cleanup_tables("pickup_requests", "homework_daily_progress")
def test_sync_reply_locks_request_row(
    no_people_residue: None,
    owner_cleanup: list[tuple[str, UUID]],
    committing_db_session: Session,
    db_engine: Engine,
    clock: FakeClock,
) -> None:
    """先鎖請求列再判斷：即使員工回覆不需更新，同步期間其他交易的 FOR UPDATE 也要等它結束。"""
    ming = make_student(committing_db_session)
    # 不用 factory 的 reply_source="staff"（會建自訂角色）：committing 測試不往 roles 寫列
    teacher = make_staff(committing_db_session, role_code="tutor")
    request = make_pickup_request(
        committing_db_session, ming, service_date=_TODAY, status="acknowledged"
    )
    request.reply_source = "staff"
    request.reply_message = "17:30 好"
    request.replied_by = teacher.id
    request.replied_at = ARCHIVED_AT
    _progress(committing_db_session, ming.id, overall="in_progress", eta=time(18, 0))
    committing_db_session.commit()
    # 依 FK 順序：請求表由 cleanup_tables 先清，再刪學生與員工
    owner_cleanup.extend([("students", ming.id), ("staff_users", teacher.id)])
    lock_sql = text("select id from public.pickup_requests where id = :id for update nowait")

    s1 = Session(bind=db_engine)
    s2 = Session(bind=db_engine)
    try:
        synced = sync_open_request_reply(s1, ming.id, _TODAY, clock=clock)
        assert synced is not None
        assert synced.reply_message == "17:30 好"
        with pytest.raises(OperationalError) as blocked:
            s2.execute(lock_sql, {"id": request.id})
        assert getattr(blocked.value.orig, "sqlstate", None) == "55P03"  # lock_not_available
        s2.rollback()
        s1.commit()
        assert s2.execute(lock_sql, {"id": request.id}).scalar_one() == request.id
        s2.rollback()
    finally:
        s1.close()
        s2.close()


# --- BACKEND-406 / 407 / 408 / 409 / 411 請求動作 ---


@pytest.fixture
def kick_off() -> Iterator[None]:
    """commit 後的 outbox kick 不實際派送（避免背景執行緒連 DB / LINE）。"""
    outbox_jobs.set_kick_mode("off")
    yield
    outbox_jobs.set_kick_mode("thread")


def _current_staff(staff: StaffUser) -> CurrentStaff:
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


def _current_parent(parent: ParentAccount) -> CurrentParent:
    return CurrentParent(
        id=parent.id,
        line_user_id=parent.line_user_id,
        display_name=parent.display_name,
        token_version=parent.token_version,
    )


@pytest.fixture
def operator(db_session: Session) -> StaffUser:
    return make_staff(db_session, permissions=["pickup:operate"], display_name="林老師")


def _family(
    db: Session, *, can_pickup: bool = True, name: str = "王小明"
) -> tuple[Any, ParentAccount]:
    student = make_student(db, name=name)
    parent = make_parent(db)
    make_guardian(db, student, parent=parent, can_pickup=can_pickup)
    return student, parent


def _parent_create(student_id: UUID, **fields: Any) -> ParentPickupRequestCreateIn:
    return ParentPickupRequestCreateIn(student_id=student_id, **fields)


def _events(db: Session, event_name: str, request_id: UUID) -> list[Notification]:
    return list(
        db.execute(
            select(Notification).where(
                Notification.event == event_name,
                Notification.payload["request_id"].astext == str(request_id),
            )
        ).scalars()
    )


def _reload_request(db: Session, request_id: UUID) -> PickupRequest:
    return db.execute(
        select(PickupRequest)
        .where(PickupRequest.id == request_id)
        .execution_options(populate_existing=True)
    ).scalar_one()


def test_create_pickup_request_auto_reply_done(
    db_session: Session, clock: FakeClock, kick_off: None
) -> None:
    ming, parent = _family(db_session)
    make_homework_progress(db_session, ming, service_date=_TODAY, overall_status="done")

    request = create_request(
        db_session, _parent_create(ming.id), actor=Actor("parent", parent.id), clock=clock
    )

    assert (request.status, request.source, request.requested_by_type) == (
        "pending",
        "parent",
        "parent",
    )
    assert request.requested_by_id == parent.id
    assert (request.reply_message, request.reply_source) == (DONE_REPLY_TEXT, "auto")
    assert (request.replied_at, request.homework_status_at_request) == (_CLOCK_NOW, "done")


def test_create_pickup_request_auto_reply_eta(
    db_session: Session, clock: FakeClock, kick_off: None
) -> None:
    ming, parent = _family(db_session)
    make_homework_progress(
        db_session, ming, service_date=_TODAY, overall_status="in_progress", ready_eta=time(17, 30)
    )

    request = create_request(
        db_session,
        _parent_create(ming.id, expected_arrival_at="17:45"),
        actor=Actor("parent", parent.id),
        clock=clock,
    )

    assert request.reply_ready_eta == time(17, 30)
    assert request.reply_message is not None
    assert request.reply_message.startswith("預計 17:30 可接送")
    assert request.expected_arrival_at == _arrival(17, 45)
    assert request.homework_status_at_request == "in_progress"


def test_create_pickup_request_no_eta_notifies_staff(
    db_session: Session, clock: FakeClock, operator: StaffUser, kick_off: None
) -> None:
    ming, parent = _family(db_session)
    bystander = make_staff(db_session, permissions=[])

    request = create_request(
        db_session,
        _parent_create(ming.id, expected_arrival_at="17:45"),
        actor=Actor("parent", parent.id),
        clock=clock,
    )

    assert request.reply_message == "已通知老師，稍後回覆預計時間"
    rows = _events(db_session, "pickup.requested", request.id)
    recipients = {row.recipient_id for row in rows}
    assert operator.id in recipients
    assert bystander.id not in recipients
    assert rows[0].payload == {
        "student_id": str(ming.id),
        "student_name": "王小明",
        "request_id": str(request.id),
        "expected_arrival_at": "17:45",
    }
    [view] = build_request_views(db_session, [request])
    assert view.needs_reply is True


def test_create_pickup_request_duplicate(
    db_session: Session, clock: FakeClock, kick_off: None
) -> None:
    ming, parent = _family(db_session)
    actor = Actor("parent", parent.id)
    first = create_request(db_session, _parent_create(ming.id), actor=actor, clock=clock)

    with pytest.raises(AppError) as exc:
        create_request(db_session, _parent_create(ming.id), actor=actor, clock=clock)
    assert (exc.value.status, exc.value.code) == (409, "pickup_request_exists")
    assert exc.value.details == {"request_id": first.id, "status": "pending"}

    first.status = "completed"
    first.completed_at = _CLOCK_NOW
    first.completion_method = "override"
    db_session.flush()
    again = create_request(db_session, _parent_create(ming.id), actor=actor, clock=clock)
    assert again.id != first.id


def test_create_pickup_request_parent_rules(
    db_session: Session, clock: FakeClock, operator: StaffUser, kick_off: None
) -> None:
    blocked, blocked_parent = _family(db_session, can_pickup=False)
    ming, parent = _family(db_session)
    other, _ = _family(db_session, name="林小安")

    with pytest.raises(AppError) as not_allowed:
        create_request(
            db_session,
            _parent_create(blocked.id),
            actor=Actor("parent", blocked_parent.id),
            clock=clock,
        )
    with pytest.raises(AppError) as idor:
        create_request(
            db_session, _parent_create(other.id), actor=Actor("parent", parent.id), clock=clock
        )
    morning = FakeClock(datetime(2026, 9, 10, 2, 0, tzinfo=UTC))  # 台北 10:00
    with pytest.raises(AppError) as closed:
        create_request(
            db_session, _parent_create(ming.id), actor=Actor("parent", parent.id), clock=morning
        )

    assert (not_allowed.value.status, not_allowed.value.code) == (403, "pickup_not_allowed")
    assert (idor.value.status, idor.value.code) == (404, "student_not_found")
    assert (closed.value.status, closed.value.code) == (409, "pickup_window_closed")
    assert closed.value.details == {"request_start": "12:00", "request_end": "19:00"}
    # 員工代建不受時段限制
    by_staff = create_request(
        db_session,
        StaffPickupRequestCreateIn(student_id=ming.id),
        actor=Actor("staff", operator.id),
        clock=morning,
    )
    assert (by_staff.source, by_staff.requested_by_type, by_staff.requested_by_id) == (
        "staff",
        "staff",
        operator.id,
    )


def test_create_pickup_request_arrival_validation(
    db_session: Session, clock: FakeClock, kick_off: None
) -> None:
    ming, parent = _family(db_session)
    actor = Actor("parent", parent.id)

    with pytest.raises(AppError) as past:
        create_request(
            db_session,
            _parent_create(ming.id, expected_arrival_at="15:00"),
            actor=actor,
            clock=clock,
        )
    with pytest.raises(AppError) as late:
        create_request(
            db_session,
            _parent_create(ming.id, expected_arrival_at="19:30"),
            actor=actor,
            clock=clock,
        )

    assert (past.value.status, past.value.code) == (422, "expected_arrival_in_past")
    assert (late.value.status, late.value.code) == (422, "expected_arrival_too_late")
    # 5 分鐘內的過去時間容許（now 16:00）
    ok = create_request(
        db_session, _parent_create(ming.id, expected_arrival_at="15:56"), actor=actor, clock=clock
    )
    assert ok.expected_arrival_at == _arrival(15, 56)


def test_create_pickup_request_attendance(
    db_session: Session, clock: FakeClock, kick_off: None
) -> None:
    on_leave, leave_parent = _family(db_session)
    leave = make_leave(db_session, on_leave, start_date=_TODAY)
    make_attendance(db_session, on_leave, service_date=_TODAY, status="leave", leave=leave)
    left, left_parent = _family(db_session)
    make_attendance(db_session, left, service_date=_TODAY, status="left")
    ming, parent = _family(db_session)

    for student, owner in ((on_leave, leave_parent), (left, left_parent)):
        with pytest.raises(AppError) as exc:
            create_request(
                db_session, _parent_create(student.id), actor=Actor("parent", owner.id), clock=clock
            )
        assert (exc.value.status, exc.value.code) == (409, "student_not_available")
    db_session.add(ClosedDay(date=_TODAY, reason="颱風假"))
    db_session.flush()
    with pytest.raises(AppError) as closed:
        create_request(
            db_session, _parent_create(ming.id), actor=Actor("parent", parent.id), clock=clock
        )
    assert (closed.value.status, closed.value.code) == (409, "not_service_day")


def test_create_pickup_request_arrived_shortcut(
    db_session: Session,
    clock: FakeClock,
    operator: StaffUser,
    kick_off: None,
    published: list[Call],
) -> None:
    ming, parent = _family(db_session)
    make_homework_progress(
        db_session, ming, service_date=_TODAY, overall_status="in_progress", ready_eta=time(17, 30)
    )

    request = create_request(
        db_session,
        _parent_create(ming.id, arrived=True),
        actor=Actor("parent", parent.id),
        clock=clock,
    )

    assert (request.status, request.arrived_at, request.expected_arrival_at) == (
        "arrived",
        _CLOCK_NOW,
        None,
    )
    assert request.reply_message is not None
    assert request.reply_message.startswith("預計 17:30 可接送")
    assert len(_events(db_session, "pickup.requested", request.id)) >= 1
    assert _events(db_session, "pickup.arrived", request.id) == []  # ws-only，不寫 notifications
    db_session.commit()
    transient = [
        m
        for ch, m in published
        if ch == [staff_channel(operator.id)] and m["type"] == "notification.transient"
    ]
    assert [m["data"]["event"] for m in transient] == ["pickup.arrived"]


def test_create_pickup_request_arrived_shortcut_existing(
    db_session: Session, clock: FakeClock, kick_off: None
) -> None:
    ming, parent = _family(db_session)
    existing = make_pickup_request(
        db_session, ming, service_date=_TODAY, status="acknowledged", requested_by=parent.id
    )

    with pytest.raises(AppError) as exc:
        create_request(
            db_session,
            _parent_create(ming.id, arrived=True),
            actor=Actor("parent", parent.id),
            clock=clock,
        )

    assert (exc.value.status, exc.value.code) == (409, "pickup_request_exists")
    assert exc.value.details == {"request_id": existing.id, "status": "acknowledged"}
    assert _reload_request(db_session, existing.id).status == "acknowledged"


@pytest.mark.cleanup_tables("pickup_requests", "notification_outbox", "notifications")
def test_create_pickup_request_concurrent(
    no_people_residue: None,
    owner_cleanup: list[tuple[str, UUID]],
    committing_db_session: Session,
    db_engine: Engine,
    clock: FakeClock,
    kick_off: None,
) -> None:
    ming = make_student(committing_db_session)
    parent = make_parent(committing_db_session)
    guardian = make_guardian(committing_db_session, ming, parent=parent)
    staff = make_staff(committing_db_session, role_code="tutor")
    committing_db_session.commit()
    owner_cleanup.extend(
        [
            ("guardians", guardian.id),
            ("students", ming.id),
            ("parent_accounts", parent.id),
            ("staff_users", staff.id),
        ]
    )

    s1 = Session(bind=db_engine)
    s2 = Session(bind=db_engine)
    s2_done = threading.Event()
    outcome: dict[str, Any] = {}

    def worker() -> None:
        try:
            outcome["s2"] = create_request(
                s2,
                StaffPickupRequestCreateIn(student_id=ming.id),
                actor=Actor("staff", staff.id),
                clock=clock,
            ).id
            s2.commit()
        except BaseException as exc:
            outcome["s2"] = exc
            s2.rollback()
        finally:
            s2_done.set()

    thread = threading.Thread(target=worker)
    try:
        outcome["s1"] = create_request(
            s1, _parent_create(ming.id), actor=Actor("parent", parent.id), clock=clock
        ).id
        thread.start()
        assert not s2_done.wait(timeout=0.5)  # s1 尚未 commit：s2 的 INSERT 等待 unique 判定
        s1.commit()
        thread.join(timeout=10)
    finally:
        s1.close()
        if thread.is_alive():
            thread.join(timeout=10)
        s2.close()

    error = outcome["s2"]
    assert isinstance(error, AppError)
    assert (error.status, error.code) == (409, "pickup_request_exists")
    assert error.details == {"request_id": outcome["s1"], "status": "pending"}
    open_count = committing_db_session.execute(
        select(func.count())
        .select_from(PickupRequest)
        .where(PickupRequest.student_id == ming.id, PickupRequest.status == "pending")
    ).scalar_one()
    assert open_count == 1


def test_reply_pickup_request_success(
    db_session: Session, clock: FakeClock, operator: StaffUser, kick_off: None
) -> None:
    ming, parent = _family(db_session)
    request = make_pickup_request(db_session, ming, service_date=_TODAY, requested_by=parent.id)
    actor = _current_staff(operator)

    replied = reply_request(
        db_session, request.id, PickupReplyIn(reply_ready_eta="18:00"), actor=actor, clock=clock
    )

    assert (replied.reply_message, replied.reply_source, replied.replied_by) == (
        "預計 18:00 可接送",
        "staff",
        operator.id,
    )
    assert (replied.status, replied.reply_ready_eta, replied.replied_at) == (
        "pending",
        time(18, 0),
        _CLOCK_NOW,
    )
    [row] = _events(db_session, "pickup.replied", request.id)
    assert (row.recipient_type, row.recipient_id) == ("parent", parent.id)
    assert row.payload["reply_message"] == "預計 18:00 可接送"

    both = reply_request(
        db_session,
        request.id,
        PickupReplyIn(reply_ready_eta="18:10", reply_message="還在訂正，晚一點"),
        actor=actor,
        clock=clock,
    )
    assert both.reply_message == "還在訂正，晚一點"


def test_reply_pickup_request_staff_created_no_notify(
    db_session: Session, clock: FakeClock, operator: StaffUser, kick_off: None
) -> None:
    ming = make_student(db_session)
    request = make_pickup_request(
        db_session, ming, service_date=_TODAY, source="staff", requested_by=operator.id
    )

    replied = reply_request(
        db_session,
        request.id,
        PickupReplyIn(reply_message="好的"),
        actor=_current_staff(operator),
        clock=clock,
    )

    assert replied.reply_source == "staff"
    assert _events(db_session, "pickup.replied", request.id) == []


def test_reply_pickup_request_terminal(
    db_session: Session, clock: FakeClock, operator: StaffUser, kick_off: None
) -> None:
    done = make_pickup_request(
        db_session, make_student(db_session), service_date=_TODAY, status="completed"
    )
    actor = _current_staff(operator)

    with pytest.raises(AppError) as terminal:
        reply_request(
            db_session, done.id, PickupReplyIn(reply_message="x"), actor=actor, clock=clock
        )
    with pytest.raises(AppError) as missing:
        reply_request(
            db_session, uuid4(), PickupReplyIn(reply_message="x"), actor=actor, clock=clock
        )

    assert (terminal.value.status, terminal.value.code) == (409, "invalid_pickup_status")
    assert (missing.value.status, missing.value.code) == (404, "pickup_request_not_found")


def test_reply_pickup_request_writes_back_eta(
    db_session: Session,
    clock: FakeClock,
    operator: StaffUser,
    kick_off: None,
    published: list[Call],
) -> None:
    ming, parent = _family(db_session)
    make_guardian(db_session, ming, parent=make_parent(db_session), name="王爸爸")
    make_homework_progress(
        db_session, ming, service_date=_TODAY, overall_status="in_progress", ready_eta=time(17, 0)
    )
    request = make_pickup_request(db_session, ming, service_date=_TODAY, requested_by=parent.id)

    reply_request(
        db_session,
        request.id,
        PickupReplyIn(reply_ready_eta="18:00"),
        actor=_current_staff(operator),
        clock=clock,
    )

    progress = _progress_of(db_session, ming.id)
    assert (progress.ready_eta, progress.eta_updated_by, progress.eta_updated_at) == (
        time(18, 0),
        operator.id,
        _CLOCK_NOW,
    )
    assert _homework_eta_events(db_session, ming.id) == []
    assert len(_events(db_session, "pickup.replied", request.id)) == 1
    db_session.commit()
    [snapshot] = [m for ch, m in published if ch == [admin_topic_channel("homework")]]
    assert snapshot["type"] == "homework.progress_updated"
    assert snapshot["data"]["progress"]["ready_eta"] == "18:00"


def test_reply_pickup_request_message_only_keeps_progress(
    db_session: Session, clock: FakeClock, operator: StaffUser, kick_off: None
) -> None:
    ming, parent = _family(db_session)
    make_homework_progress(db_session, ming, service_date=_TODAY, ready_eta=time(17, 0))
    request = make_pickup_request(db_session, ming, service_date=_TODAY, requested_by=parent.id)
    hua, hua_parent = _family(db_session, name="陳小華")
    no_progress = make_pickup_request(
        db_session, hua, service_date=_TODAY, requested_by=hua_parent.id
    )
    actor = _current_staff(operator)

    reply_request(
        db_session, request.id, PickupReplyIn(reply_message="稍等十分鐘"), actor=actor, clock=clock
    )
    reply_request(
        db_session, no_progress.id, PickupReplyIn(reply_ready_eta="18:00"), actor=actor, clock=clock
    )

    kept = _progress_of(db_session, ming.id)
    assert (kept.ready_eta, kept.eta_updated_at) == (time(17, 0), datetime(2026, 8, 1, tzinfo=UTC))
    created = _progress_of(db_session, hua.id)
    assert (created.ready_eta, created.overall_status) == (time(18, 0), "not_started")


def test_reply_pickup_request_rollback_atomic(
    db_session: Session, clock: FakeClock, operator: StaffUser, kick_off: None
) -> None:
    ming, parent = _family(db_session)
    make_homework_progress(db_session, ming, service_date=_TODAY, ready_eta=time(17, 0))
    request = make_pickup_request(db_session, ming, service_date=_TODAY, requested_by=parent.id)
    db_session.commit()

    reply_request(
        db_session,
        request.id,
        PickupReplyIn(reply_ready_eta="18:00"),
        actor=_current_staff(operator),
        clock=clock,
    )
    db_session.rollback()

    assert _reload_request(db_session, request.id).reply_source is None
    assert _progress_of(db_session, ming.id).ready_eta == time(17, 0)


def _progress_of(db: Session, student_id: UUID) -> HomeworkDailyProgress:
    return db.execute(
        select(HomeworkDailyProgress)
        .where(
            HomeworkDailyProgress.student_id == student_id,
            HomeworkDailyProgress.service_date == _TODAY,
        )
        .execution_options(populate_existing=True)
    ).scalar_one()


def _homework_eta_events(db: Session, student_id: UUID) -> list[Notification]:
    return list(
        db.execute(
            select(Notification).where(
                Notification.event == "homework.eta_updated",
                Notification.payload["student_id"].astext == str(student_id),
            )
        ).scalars()
    )


def test_acknowledge_pickup_request_notifies(
    db_session: Session, clock: FakeClock, operator: StaffUser, kick_off: None
) -> None:
    ming, parent = _family(db_session)
    request = make_pickup_request(
        db_session,
        ming,
        service_date=_TODAY,
        requested_by=parent.id,
        reply_source="auto",
        reply_message="預計 17:30 可接送",
    )

    out = acknowledge_request(db_session, request.id, actor=_current_staff(operator), clock=clock)

    assert out.status == "acknowledged"
    [row] = _events(db_session, "pickup.replied", request.id)
    assert (row.recipient_id, row.payload["reply_message"]) == (parent.id, "預計 17:30 可接送")


def test_acknowledge_pickup_request_default_message(
    db_session: Session, clock: FakeClock, operator: StaffUser, kick_off: None
) -> None:
    ming, parent = _family(db_session)
    request = make_pickup_request(db_session, ming, service_date=_TODAY, requested_by=parent.id)

    acknowledge_request(db_session, request.id, actor=_current_staff(operator), clock=clock)

    [row] = _events(db_session, "pickup.replied", request.id)
    assert row.payload["reply_message"] == "老師已確認接送請求"


def test_acknowledge_pickup_request_after_staff_reply(
    db_session: Session, clock: FakeClock, operator: StaffUser, kick_off: None
) -> None:
    ming, parent = _family(db_session)
    replied = make_pickup_request(
        db_session,
        ming,
        service_date=_TODAY,
        requested_by=parent.id,
        reply_source="staff",
        reply_message="好",
    )
    by_staff = make_pickup_request(
        db_session,
        make_student(db_session),
        service_date=_TODAY,
        source="staff",
        requested_by=operator.id,
    )
    actor = _current_staff(operator)

    acknowledge_request(db_session, replied.id, actor=actor, clock=clock)
    acknowledge_request(db_session, by_staff.id, actor=actor, clock=clock)

    assert _events(db_session, "pickup.replied", replied.id) == []
    assert _events(db_session, "pickup.replied", by_staff.id) == []


def test_acknowledge_pickup_request_invalid(
    db_session: Session, clock: FakeClock, operator: StaffUser, kick_off: None
) -> None:
    actor = _current_staff(operator)
    for status in ("acknowledged", "arrived"):
        request = make_pickup_request(
            db_session,
            make_student(db_session),
            service_date=_TODAY,
            status=status,
            reply_source="staff",
        )
        with pytest.raises(AppError) as exc:
            acknowledge_request(db_session, request.id, actor=actor, clock=clock)
        assert (exc.value.status, exc.value.code) == (409, "invalid_pickup_status")


def test_mark_arrived_success(
    db_session: Session,
    clock: FakeClock,
    operator: StaffUser,
    kick_off: None,
    published: list[Call],
) -> None:
    ming, parent = _family(db_session)
    other_guardian = make_parent(db_session)
    make_guardian(db_session, ming, parent=other_guardian, name="王爸爸")
    request = make_pickup_request(
        db_session,
        ming,
        service_date=_TODAY,
        status="acknowledged",
        reply_source="staff",
        requested_by=parent.id,
    )

    # 同一小孩的任一綁定家長都可以按
    out = mark_arrived(db_session, request.id, parent=_current_parent(other_guardian), clock=clock)

    assert (out.status, out.arrived_at) == ("arrived", _CLOCK_NOW)
    assert _events(db_session, "pickup.arrived", request.id) == []
    db_session.commit()
    transient = [
        m
        for ch, m in published
        if ch == [staff_channel(operator.id)] and m["type"] == "notification.transient"
    ]
    assert len(transient) == 1
    assert transient[0]["data"]["payload"]["request_id"] == str(request.id)


def test_mark_arrived_twice(db_session: Session, clock: FakeClock, kick_off: None) -> None:
    ming, parent = _family(db_session)
    request = make_pickup_request(db_session, ming, service_date=_TODAY, requested_by=parent.id)
    mark_arrived(db_session, request.id, parent=_current_parent(parent), clock=clock)

    with pytest.raises(AppError) as exc:
        mark_arrived(db_session, request.id, parent=_current_parent(parent), clock=clock)

    assert (exc.value.status, exc.value.code) == (409, "invalid_pickup_status")


def test_mark_arrived_idor(db_session: Session, clock: FakeClock, kick_off: None) -> None:
    _, parent_a = _family(db_session)
    other, parent_b = _family(db_session, name="林小安")
    request = make_pickup_request(db_session, other, service_date=_TODAY, requested_by=parent_b.id)

    with pytest.raises(AppError) as exc:
        mark_arrived(db_session, request.id, parent=_current_parent(parent_a), clock=clock)

    assert (exc.value.status, exc.value.code) == (404, "pickup_request_not_found")
    assert _reload_request(db_session, request.id).status == "pending"


def test_cancel_pickup_request_parent(
    db_session: Session, clock: FakeClock, kick_off: None, published: list[Call]
) -> None:
    ming, parent = _family(db_session)
    request = make_pickup_request(
        db_session,
        ming,
        service_date=_TODAY,
        status="arrived",
        reply_source="staff",
        requested_by=parent.id,
    )

    out = cancel_request(
        db_session,
        request.id,
        PickupCancelIn(reason="臨時改由爺爺接"),
        actor=Actor("parent", parent.id),
        clock=clock,
    )

    assert (out.status, out.cancel_reason, out.cancelled_at) == (
        "cancelled",
        "臨時改由爺爺接",
        _CLOCK_NOW,
    )
    db_session.commit()
    pushed = [m for ch, m in published if ch == [admin_topic_channel("pickup")]]
    assert pushed[-1]["data"]["status"] == "cancelled"


def test_cancel_pickup_request_terminal(
    db_session: Session, clock: FakeClock, operator: StaffUser, kick_off: None
) -> None:
    done = make_pickup_request(
        db_session, make_student(db_session), service_date=_TODAY, status="completed"
    )

    with pytest.raises(AppError) as exc:
        cancel_request(db_session, done.id, None, actor=Actor("staff", operator.id), clock=clock)

    assert (exc.value.status, exc.value.code) == (409, "invalid_pickup_status")


def test_cancel_pickup_request_idor_and_staff(
    db_session: Session, clock: FakeClock, operator: StaffUser, kick_off: None
) -> None:
    _, parent_a = _family(db_session)
    other, parent_b = _family(db_session, name="林小安")
    request = make_pickup_request(db_session, other, service_date=_TODAY, requested_by=parent_b.id)

    with pytest.raises(AppError) as idor:
        cancel_request(
            db_session, request.id, None, actor=Actor("parent", parent_a.id), clock=clock
        )
    assert (idor.value.status, idor.value.code) == (404, "pickup_request_not_found")
    assert _reload_request(db_session, request.id).status == "pending"

    out = cancel_request(
        db_session, request.id, None, actor=Actor("staff", operator.id), clock=clock
    )
    assert (out.status, out.cancel_reason) == ("cancelled", None)


def test_cancel_pickup_request_notifies_staff(
    db_session: Session, clock: FakeClock, operator: StaffUser, kick_off: None
) -> None:
    ming, parent = _family(db_session)
    request = make_pickup_request(db_session, ming, service_date=_TODAY, requested_by=parent.id)

    cancel_request(
        db_session,
        request.id,
        PickupCancelIn(reason="臨時改由爺爺接"),
        actor=Actor("parent", parent.id),
        clock=clock,
    )

    rows = _events(db_session, "pickup.cancelled", request.id)
    assert operator.id in {row.recipient_id for row in rows}
    assert {row.recipient_type for row in rows} == {"staff"}
    assert rows[0].payload["cancelled_by_label"] == "家長"
    assert rows[0].payload["reason"] == "臨時改由爺爺接"


def test_cancel_pickup_request_notifies_parent(
    db_session: Session, clock: FakeClock, operator: StaffUser, kick_off: None
) -> None:
    ming, parent = _family(db_session)
    by_parent = make_pickup_request(db_session, ming, service_date=_TODAY, requested_by=parent.id)
    by_staff = make_pickup_request(
        db_session,
        make_student(db_session),
        service_date=_TODAY,
        source="staff",
        requested_by=operator.id,
    )
    actor = Actor("staff", operator.id)

    cancel_request(db_session, by_parent.id, None, actor=actor, clock=clock)
    cancel_request(db_session, by_staff.id, None, actor=actor, clock=clock)

    [row] = _events(db_session, "pickup.cancelled", by_parent.id)
    assert (row.recipient_type, row.recipient_id) == ("parent", parent.id)
    assert row.payload["cancelled_by_label"] == "老師"
    outbox = db_session.execute(
        select(func.count())
        .select_from(NotificationOutbox)
        .where(NotificationOutbox.notification_id == row.id)
    ).scalar_one()
    assert outbox == 1
    assert _events(db_session, "pickup.cancelled", by_staff.id) == []
