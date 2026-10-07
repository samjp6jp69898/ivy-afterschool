"""BACKEND-373 / 384 / 374 / 383 / 376 / 382：homework_service（進度列鎖定、家長端當日作業明細、
ws 快照推播、作業進度看板、整體完成副作用、設定預計可接送時間、重算整體進度、手動標整體完成）。
BACKEND-377：新增單一學生作業項目。BACKEND-378：整班批次新增同一份作業。BACKEND-379：修改作業項目。

推播測試以 monkeypatch 記錄 publish_threadsafe；commit 走 db_session（savepoint 模式的 commit
同樣觸發 before_commit / after_commit，見 BACKEND-006）。
"""

import json
import threading
from collections.abc import Iterator
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, event, func, select, text, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.api.deps import CurrentStaff
from app.core.errors import AppError
from app.core.tx_hooks import install_tx_hooks
from app.models.account import StaffUser
from app.models.homework import HomeworkDailyProgress, HomeworkItem
from app.models.notifications import Notification
from app.models.pickup import PickupRequest
from app.models.reference import Subject
from app.notifications import outbox_jobs
from app.realtime import publish as publish_module
from app.realtime.publish import admin_topic_channel, student_channel
from app.repositories.students import list_active_student_ids
from app.schemas.homework import (
    BoardOut,
    BoardQuery,
    BoardStudentOut,
    HomeworkBatchCreateIn,
    HomeworkItemCreateIn,
    HomeworkItemUpdateIn,
)
from app.services import homework_service
from app.services.homework_service import (
    ProgressChange,
    batch_create_items,
    broadcast_homework_snapshot,
    create_item,
    get_board,
    get_child_homework,
    handle_homework_done,
    lock_progress_row,
    recompute_progress,
    set_overall_status,
    set_ready_eta_and_note,
    update_item,
)
from app.services.settings_service import clear_settings_cache
from tests.integration.db.conftest import connect_owner
from tests.support.factories import (
    make_attendance,
    make_class,
    make_guardian,
    make_homework_item,
    make_homework_progress,
    make_leave,
    make_parent,
    make_pickup_request,
    make_staff,
    make_student,
)
from tests.support.fake_clock import FakeClock

_DAY = date(2026, 9, 1)
# lock_timeout 一律 SET LOCAL：連線會回到 pool，session 級 SET 會污染之後借到該連線的測試


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
        created_id = lock_progress_row(s1, student.id, _DAY).id
        s2.execute(text("set local lock_timeout = '200ms'"))
        with pytest.raises(OperationalError) as blocked:
            lock_progress_row(s2, student.id, _DAY)
        assert getattr(blocked.value.orig, "sqlstate", None) == "55P03"  # lock_not_available
        s2.rollback()

        s1.commit()
        s2.execute(text("set local lock_timeout = '200ms'"))
        retried_id = lock_progress_row(s2, student.id, _DAY).id
        s2.commit()
    finally:
        s1.close()
        s2.close()

    assert retried_id == created_id


@pytest.mark.cleanup_tables("homework_daily_progress")
def test_lock_progress_row_blocks_existing_row(
    owner_cleanup_students: list[UUID], committing_db_session: Session, db_engine: Engine
) -> None:
    """已有列時：s1 持有 FOR UPDATE，s2 的 SELECT FOR UPDATE 被擋；s1 commit 後才能取得。"""
    student = make_student(committing_db_session)
    existing = make_homework_progress(committing_db_session, student, service_date=_DAY)
    committing_db_session.commit()
    owner_cleanup_students.append(student.id)
    existing_id = existing.id

    s1 = Session(bind=db_engine)
    s2 = Session(bind=db_engine)
    try:
        assert lock_progress_row(s1, student.id, _DAY).id == existing_id
        s2.execute(text("set local lock_timeout = '200ms'"))
        with pytest.raises(OperationalError) as blocked:
            lock_progress_row(s2, student.id, _DAY)
        assert getattr(blocked.value.orig, "sqlstate", None) == "55P03"
        s2.rollback()

        s1.commit()
        retried_id = lock_progress_row(s2, student.id, _DAY).id
        s2.commit()
    finally:
        s1.close()
        s2.close()

    assert retried_id == existing_id


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
        assert not b_done.wait(timeout=0.5), errors  # A 尚未 commit：B 被擋住
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


# --- BACKEND-374 broadcast_homework_snapshot ---

Call = tuple[list[str], dict[str, Any]]


@pytest.fixture
def published(monkeypatch: pytest.MonkeyPatch) -> list[Call]:
    install_tx_hooks()
    calls: list[Call] = []

    def record(channels: list[str], message: dict[str, Any]) -> None:
        calls.append((list(channels), dict(message)))

    monkeypatch.setattr(publish_module, "publish_threadsafe", record)
    return calls


def _on(calls: list[Call], channel: str) -> list[dict[str, Any]]:
    return [message for channels, message in calls if channels == [channel]]


def test_broadcast_homework_snapshot_split(
    db_session: Session, fake_clock: FakeClock, published: list[Call]
) -> None:
    ming = make_student(db_session, name="王小明")
    teacher = make_staff(db_session, display_name="林老師")
    item = make_homework_item(
        db_session, ming, service_date=_DAY, status="doing", subject=_subject(db_session, "數學")
    )
    progress = make_homework_progress(
        db_session,
        ming,
        service_date=_DAY,
        overall_status="in_progress",
        ready_eta=time(17, 30),
        note="剩訂正",
    )
    progress.eta_updated_by = teacher.id
    db_session.flush()

    broadcast_homework_snapshot(db_session, ming.id, _DAY, clock=fake_clock)
    assert published == []
    db_session.commit()

    assert len(published) == 2
    [admin] = _on(published, admin_topic_channel("homework"))
    [parent] = _on(published, student_channel(ming.id))
    assert admin["type"] == parent["type"] == "homework.progress_updated"
    data = admin["data"]
    assert (data["student_id"], data["service_date"]) == (str(ming.id), "2026-09-01")
    assert data["items"][0]["title"] == "數學習作 p.12-13"
    assert data["items"][0]["id"] == str(item.id)
    assert data["items"][0]["subject_name"] == "數學"
    assert data["progress"] == {
        "student_id": str(ming.id),
        "service_date": "2026-09-01",
        "overall_status": "in_progress",
        "ready_eta": "17:30",
        "note": "剩訂正",
        "eta_updated_at": datetime(2026, 8, 1, tzinfo=UTC).isoformat(),
        "eta_updated_by_name": "林老師",
    }
    assert parent["data"] == {
        "student_id": str(ming.id),
        "date": "2026-09-01",
        "items": [{"title": "數學習作 p.12-13", "subject_name": "數學", "status": "doing"}],
        "overall_status": "in_progress",
        "ready_eta": "17:30",
        "note": "剩訂正",
    }
    assert "eta_updated_by_name" not in parent["data"]
    assert "id" not in parent["data"]["items"][0]


def test_broadcast_homework_snapshot_without_progress_row(
    db_session: Session, fake_clock: FakeClock, published: list[Call]
) -> None:
    ming = make_student(db_session)

    broadcast_homework_snapshot(db_session, ming.id, _DAY, clock=fake_clock)
    db_session.commit()

    [admin] = _on(published, admin_topic_channel("homework"))
    assert admin["data"]["items"] == []
    assert admin["data"]["progress"]["overall_status"] == "not_started"
    assert admin["data"]["progress"]["eta_updated_by_name"] is None
    [parent] = _on(published, student_channel(ming.id))
    assert (parent["data"]["overall_status"], parent["data"]["ready_eta"]) == ("not_started", None)


def test_broadcast_homework_snapshot_dedupe(
    db_session: Session, fake_clock: FakeClock, published: list[Call]
) -> None:
    ming = make_student(db_session)
    hua = make_student(db_session, name="陳小華")
    item = make_homework_item(db_session, ming, service_date=_DAY)

    broadcast_homework_snapshot(db_session, ming.id, _DAY, clock=fake_clock)
    item.status = "done"
    db_session.flush()
    broadcast_homework_snapshot(db_session, ming.id, _DAY, clock=fake_clock)
    # 不同學生 / 不同日期各自推送
    broadcast_homework_snapshot(db_session, hua.id, _DAY, clock=fake_clock)
    broadcast_homework_snapshot(db_session, ming.id, date(2026, 9, 2), clock=fake_clock)
    db_session.commit()

    admin = _on(published, admin_topic_channel("homework"))
    assert len(admin) == 3
    [ming_today] = [
        m["data"]
        for m in admin
        if (m["data"]["student_id"], m["data"]["service_date"]) == (str(ming.id), "2026-09-01")
    ]
    assert [i["status"] for i in ming_today["items"]] == ["done"]
    assert len(_on(published, student_channel(ming.id))) == 2

    # 下一個交易重新計算，不沿用上一個交易的去重紀錄
    published.clear()
    broadcast_homework_snapshot(db_session, ming.id, _DAY, clock=fake_clock)
    db_session.commit()
    assert len(_on(published, admin_topic_channel("homework"))) == 1


def test_broadcast_homework_snapshot_rollback(
    db_session: Session, fake_clock: FakeClock, published: list[Call]
) -> None:
    ming = make_student(db_session)
    db_session.commit()

    broadcast_homework_snapshot(db_session, ming.id, _DAY, clock=fake_clock)
    db_session.rollback()
    assert published == []

    # rollback 清掉的推播不會在下一次 commit 時補送
    db_session.commit()
    assert published == []


# --- BACKEND-383 get_board ---


@pytest.fixture
def fresh_settings() -> Iterator[None]:
    clear_settings_cache()
    yield
    clear_settings_cache()


def _card(board: BoardOut, student_id: UUID) -> BoardStudentOut:
    [card] = [c for c in board.students if c.student_id == student_id]
    return card


def test_homework_board_cards(
    db_session: Session, fake_clock: FakeClock, fresh_settings: None
) -> None:
    class_a = make_class(db_session, name="A班")
    ming = make_student(db_session, name="王小明", student_no="H-001", class_=class_a)
    hua = make_student(db_session, name="陳小華", student_no="H-002", class_=class_a)
    teacher = make_staff(db_session, display_name="林老師")
    make_homework_item(
        db_session, ming, service_date=_DAY, title="數學習作", status="done", sort_order=20
    )
    make_homework_item(db_session, ming, service_date=_DAY, title="國語生字", sort_order=10)
    make_homework_item(db_session, ming, service_date=date(2026, 9, 2), title="隔天的作業")
    progress = make_homework_progress(
        db_session, ming, service_date=_DAY, overall_status="in_progress", ready_eta=time(17, 30)
    )
    progress.eta_updated_by = teacher.id
    make_attendance(db_session, ming, service_date=_DAY, status="present")
    make_attendance(db_session, hua, service_date=_DAY)
    db_session.flush()

    board = get_board(db_session, BoardQuery(date=_DAY, class_id=class_a.id), clock=fake_clock)

    assert board.date == _DAY
    assert [c.name for c in board.students] == ["王小明", "陳小華"]
    ming_card, hua_card = board.students
    assert (ming_card.student_no, ming_card.class_id, ming_card.class_name) == (
        "H-001",
        class_a.id,
        "A班",
    )
    assert ming_card.attendance_status == "present"
    assert [i.title for i in ming_card.items] == ["國語生字", "數學習作"]
    assert ming_card.progress.overall_status == "in_progress"
    assert ming_card.progress.ready_eta == "17:30"
    assert ming_card.progress.eta_updated_by_name == "林老師"
    assert hua_card.attendance_status == "expected"
    assert hua_card.items == []
    assert hua_card.progress.overall_status == "not_started"
    assert (hua_card.progress.ready_eta, hua_card.progress.student_id) == (None, hua.id)
    assert board.summary.model_dump() == {
        "total": 2,
        "done": 0,
        "in_progress": 1,
        "not_started": 1,
    }


def test_homework_board_defaults_to_today(
    db_session: Session, fake_clock: FakeClock, fresh_settings: None
) -> None:
    """跨午夜：UTC 9/1 16:30 已是台北 9/2。"""
    class_a = make_class(db_session)
    ming = make_student(db_session, class_=class_a)
    make_homework_item(db_session, ming, service_date=date(2026, 9, 2), title="隔天的作業")
    fake_clock.set(datetime(2026, 9, 1, 16, 30, tzinfo=UTC))

    board = get_board(db_session, BoardQuery(class_id=class_a.id), clock=fake_clock)

    assert board.date == date(2026, 9, 2)
    assert [i.title for i in _card(board, ming.id).items] == ["隔天的作業"]
    assert _card(board, ming.id).attendance_status is None


def test_homework_board_summary_excludes_leave(
    db_session: Session, fake_clock: FakeClock, fresh_settings: None
) -> None:
    class_a = make_class(db_session)
    ming = make_student(db_session, name="王小明", class_=class_a)
    make_student(db_session, name="陳小華", class_=class_a)
    an = make_student(db_session, name="林小安", class_=class_a)
    mei = make_student(db_session, name="張小美", class_=class_a)
    leave = make_leave(db_session, an, start_date=_DAY)
    make_attendance(db_session, an, service_date=_DAY, status="leave", leave=leave)
    make_homework_progress(db_session, an, service_date=_DAY, overall_status="done")
    make_attendance(db_session, mei, service_date=_DAY, status="absent")
    make_homework_progress(db_session, ming, service_date=_DAY, overall_status="done")
    # 退班 / 封存學生不出現
    gone = make_student(db_session, name="李小東", class_=class_a)
    gone.status = "withdrawn"
    gone.withdrawn_on = _DAY
    make_student(db_session, name="周小西", class_=class_a, archived=True)
    db_session.flush()

    board = get_board(db_session, BoardQuery(date=_DAY, class_id=class_a.id), clock=fake_clock)

    assert {c.name for c in board.students} == {"王小明", "陳小華", "林小安", "張小美"}
    assert _card(board, an.id).attendance_status == "leave"
    assert board.summary.model_dump() == {
        "total": 2,
        "done": 1,
        "in_progress": 0,
        "not_started": 1,
    }


def test_homework_board_filter_and_queries(
    db_session: Session, fake_clock: FakeClock, fresh_settings: None
) -> None:
    class_a = make_class(db_session)
    class_b = make_class(db_session)
    other = make_student(db_session, name="陳小華", class_=class_a)
    ids = set()
    for index in range(30):
        student = make_student(db_session, name=f"學生{index}", class_=class_b)
        ids.add(student.id)
        make_homework_item(db_session, student, service_date=_DAY, title=f"作業{index}")
        make_homework_progress(db_session, student, service_date=_DAY, overall_status="in_progress")
        make_attendance(db_session, student, service_date=_DAY, status="present")
    statements: list[str] = []

    def record(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        statements.append(statement)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", record)
    try:
        board = get_board(db_session, BoardQuery(date=_DAY, class_id=class_b.id), clock=fake_clock)
    finally:
        event.remove(engine, "before_cursor_execute", record)

    assert {c.student_id for c in board.students} == ids
    assert other.id not in {c.student_id for c in board.students}
    assert all(len(c.items) == 1 for c in board.students)
    assert board.summary.in_progress == 30
    assert len(statements) <= 5


def test_homework_board_includes_window(
    db_session: Session, fake_clock: FakeClock, fresh_settings: None
) -> None:
    default = get_board(db_session, BoardQuery(date=_DAY), clock=fake_clock)
    assert default.window.model_dump() == {"past_days": 30, "future_days": 7}

    db_session.execute(
        text(
            "update public.system_settings set value = cast(:value as jsonb) "
            "where key = 'homework.window'"
        ),
        {"value": '{"past_days": 10, "future_days": 3}'},
    )
    clear_settings_cache()
    changed = get_board(db_session, BoardQuery(date=_DAY), clock=fake_clock)

    assert changed.window.model_dump() == {"past_days": 10, "future_days": 3}


# --- BACKEND-376 handle_homework_done / BACKEND-382 set_ready_eta_and_note ---

_NOW = datetime(2026, 9, 1, 7, 0, tzinfo=UTC)  # 台北 15:00，_DAY 當天


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(_NOW)


@pytest.fixture
def kick_off() -> Iterator[None]:
    """commit 後的 outbox kick 不實際派送（避免背景執行緒連 DB / LINE）。"""
    outbox_jobs.set_kick_mode("off")
    yield
    outbox_jobs.set_kick_mode("thread")


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
    return _current(make_staff(db_session, permissions=["homework:write"], display_name="林老師"))


def _notifications(db: Session, event_name: str, student_id: UUID) -> list[Notification]:
    return list(
        db.execute(
            select(Notification).where(
                Notification.event == event_name,
                Notification.payload["student_id"].astext == str(student_id),
            )
        ).scalars()
    )


def _progress_row(db: Session, student_id: UUID) -> HomeworkDailyProgress:
    return db.execute(
        select(HomeworkDailyProgress)
        .where(
            HomeworkDailyProgress.student_id == student_id,
            HomeworkDailyProgress.service_date == _DAY,
        )
        .execution_options(populate_existing=True)
    ).scalar_one()


def test_handle_homework_done_notifies(
    db_session: Session, clock: FakeClock, kick_off: None
) -> None:
    ming = make_student(db_session, name="王小明")
    p1 = make_parent(db_session)
    make_guardian(db_session, ming, parent=p1)
    make_homework_progress(db_session, ming, service_date=_DAY, overall_status="done")

    handle_homework_done(db_session, ming.id, _DAY, clock=clock)

    [row] = _notifications(db_session, "homework.done", ming.id)
    assert (row.recipient_type, row.recipient_id) == ("parent", p1.id)
    assert row.title == "王小明 作業已完成"
    assert row.payload == {"student_id": str(ming.id), "student_name": "王小明"}


def test_handle_homework_done_syncs_pickup(
    db_session: Session, clock: FakeClock, kick_off: None
) -> None:
    ming = make_student(db_session)
    request = make_pickup_request(
        db_session,
        ming,
        service_date=_DAY,
        reply_source="auto",
        reply_message="已通知老師，稍後回覆預計時間",
    )
    make_homework_progress(db_session, ming, service_date=_DAY, overall_status="done")

    handle_homework_done(db_session, ming.id, _DAY, clock=clock)

    refreshed = db_session.execute(
        select(PickupRequest)
        .where(PickupRequest.id == request.id)
        .execution_options(populate_existing=True)
    ).scalar_one()
    assert (refreshed.reply_message, refreshed.reply_source) == ("作業已完成，可以接送", "auto")


def test_set_ready_eta_notifies(
    db_session: Session,
    clock: FakeClock,
    actor: CurrentStaff,
    kick_off: None,
    published: list[Call],
) -> None:
    ming = make_student(db_session, name="王小明")
    make_guardian(db_session, ming, parent=make_parent(db_session))
    make_homework_progress(db_session, ming, service_date=_DAY, overall_status="in_progress")

    out = set_ready_eta_and_note(
        db_session,
        ming.id,
        _DAY,
        ready_eta=time(17, 30),
        note="剩數學訂正",
        actor=actor,
        clock=clock,
    )

    assert (out.ready_eta, out.note, out.eta_updated_at) == ("17:30", "剩數學訂正", _NOW)
    assert out.eta_updated_by_name == "林老師"
    row = _progress_row(db_session, ming.id)
    assert (row.ready_eta, row.eta_updated_by) == (time(17, 30), actor.id)
    [notification] = _notifications(db_session, "homework.eta_updated", ming.id)
    assert notification.body == "預計 17:30 可接送。\n剩數學訂正"
    assert notification.payload == {
        "student_id": str(ming.id),
        "student_name": "王小明",
        "ready_eta": "17:30",
        "note": "剩數學訂正",
    }
    db_session.commit()
    [snapshot] = _on(published, admin_topic_channel("homework"))
    assert snapshot["data"]["progress"]["ready_eta"] == "17:30"


def test_set_ready_eta_same_value_no_notify(
    db_session: Session, clock: FakeClock, actor: CurrentStaff, kick_off: None
) -> None:
    ming = make_student(db_session)
    make_guardian(db_session, ming, parent=make_parent(db_session))

    set_ready_eta_and_note(
        db_session, ming.id, _DAY, ready_eta=time(17, 30), actor=actor, clock=clock
    )
    clock.advance(minutes=10)
    set_ready_eta_and_note(
        db_session, ming.id, _DAY, ready_eta=time(17, 30), actor=actor, clock=clock
    )

    assert len(_notifications(db_session, "homework.eta_updated", ming.id)) == 1
    assert _progress_row(db_session, ming.id).eta_updated_at == _NOW  # 同值不更新修改時間


def test_set_ready_eta_note_only(
    db_session: Session, clock: FakeClock, actor: CurrentStaff, kick_off: None
) -> None:
    ming = make_student(db_session)
    make_guardian(db_session, ming, parent=make_parent(db_session))
    make_homework_progress(db_session, ming, service_date=_DAY, ready_eta=time(17, 0))

    out = set_ready_eta_and_note(db_session, ming.id, _DAY, note="剩國語", actor=actor, clock=clock)

    assert (out.note, out.ready_eta) == ("剩國語", "17:00")
    assert _notifications(db_session, "homework.eta_updated", ming.id) == []


def test_set_ready_eta_clear_no_notify_but_syncs(
    db_session: Session, clock: FakeClock, actor: CurrentStaff, kick_off: None
) -> None:
    ming = make_student(db_session)
    make_guardian(db_session, ming, parent=make_parent(db_session))
    make_homework_progress(
        db_session, ming, service_date=_DAY, overall_status="in_progress", ready_eta=time(17, 0)
    )
    request = make_pickup_request(
        db_session,
        ming,
        service_date=_DAY,
        reply_source="auto",
        reply_message="預計 17:00 可接送",
        reply_ready_eta=time(17, 0),
    )

    out = set_ready_eta_and_note(
        db_session, ming.id, _DAY, ready_eta=None, actor=actor, clock=clock
    )

    assert out.ready_eta is None
    assert _notifications(db_session, "homework.eta_updated", ming.id) == []
    refreshed = db_session.execute(
        select(PickupRequest)
        .where(PickupRequest.id == request.id)
        .execution_options(populate_existing=True)
    ).scalar_one()
    # 清除 ETA → 自動回覆改回「已通知老師」（seed homework.defaults）
    assert (refreshed.reply_ready_eta, refreshed.reply_message) == (
        None,
        "已通知老師，稍後回覆預計時間",
    )


def test_set_ready_eta_when_done(
    db_session: Session, clock: FakeClock, actor: CurrentStaff, kick_off: None
) -> None:
    ming = make_student(db_session)
    make_guardian(db_session, ming, parent=make_parent(db_session))
    make_homework_progress(db_session, ming, service_date=_DAY, overall_status="done")

    set_ready_eta_and_note(
        db_session, ming.id, _DAY, ready_eta=time(18, 0), actor=actor, clock=clock
    )

    assert _notifications(db_session, "homework.eta_updated", ming.id) == []


def test_set_ready_eta_other_day_no_notify(
    db_session: Session, clock: FakeClock, actor: CurrentStaff, kick_off: None
) -> None:
    ming = make_student(db_session)
    make_guardian(db_session, ming, parent=make_parent(db_session))

    out = set_ready_eta_and_note(
        db_session, ming.id, date(2026, 9, 2), ready_eta=time(18, 0), actor=actor, clock=clock
    )

    assert (out.service_date, out.ready_eta) == (date(2026, 9, 2), "18:00")
    assert _notifications(db_session, "homework.eta_updated", ming.id) == []


def test_set_ready_eta_errors(
    db_session: Session, clock: FakeClock, actor: CurrentStaff, kick_off: None
) -> None:
    ming = make_student(db_session)

    with pytest.raises(AppError) as missing:
        set_ready_eta_and_note(db_session, uuid4(), _DAY, note="x", actor=actor, clock=clock)
    with pytest.raises(AppError) as too_far:
        # seed homework.window future_days = 7
        set_ready_eta_and_note(
            db_session, ming.id, date(2026, 9, 9), note="x", actor=actor, clock=clock
        )
    with pytest.raises(ValueError, match="ready_eta"):
        set_ready_eta_and_note(db_session, ming.id, _DAY, actor=actor, clock=clock)

    assert (missing.value.status, missing.value.code) == (404, "student_not_found")
    assert (too_far.value.status, too_far.value.code) == (422, "invalid_service_date")


def test_set_ready_eta_syncs_pickup(
    db_session: Session, clock: FakeClock, actor: CurrentStaff, kick_off: None
) -> None:
    ming = make_student(db_session)
    request = make_pickup_request(
        db_session,
        ming,
        service_date=_DAY,
        reply_source="auto",
        reply_message="已通知老師，稍後回覆預計時間",
    )

    set_ready_eta_and_note(
        db_session, ming.id, _DAY, ready_eta=time(17, 30), actor=actor, clock=clock
    )

    refreshed = db_session.execute(
        select(PickupRequest)
        .where(PickupRequest.id == request.id)
        .execution_options(populate_existing=True)
    ).scalar_one()
    assert refreshed.reply_ready_eta == time(17, 30)
    assert refreshed.reply_message is not None
    assert refreshed.reply_message.startswith("預計 17:30 可接送")


# --- BACKEND-375 recompute_progress / BACKEND-381 set_overall_status ---


def _with_parent(db: Session, name: str = "王小明") -> Any:
    student = make_student(db, name=name)
    make_guardian(db, student, parent=make_parent(db))
    return student


def test_recompute_progress_derives(db_session: Session, clock: FakeClock, kick_off: None) -> None:
    ming = make_student(db_session)
    make_homework_item(db_session, ming, service_date=_DAY)
    make_homework_item(db_session, ming, service_date=_DAY, status="done", title="國語生字")

    change = recompute_progress(db_session, ming.id, _DAY, clock=clock)

    assert isinstance(change, ProgressChange)
    assert (change.old_status, change.new_status) == ("not_started", "in_progress")
    assert _progress_row(db_session, ming.id).overall_status == "in_progress"


def test_recompute_progress_done_notifies_today(
    db_session: Session, clock: FakeClock, kick_off: None, published: list[Call]
) -> None:
    ming = _with_parent(db_session)
    item = make_homework_item(db_session, ming, service_date=_DAY, status="doing")
    recompute_progress(db_session, ming.id, _DAY, clock=clock)
    item.status = "done"
    db_session.flush()

    change = recompute_progress(db_session, ming.id, _DAY, clock=clock)
    again = recompute_progress(db_session, ming.id, _DAY, clock=clock)

    assert (change.old_status, change.new_status) == ("in_progress", "done")
    assert (again.old_status, again.new_status) == ("done", "done")
    assert len(_notifications(db_session, "homework.done", ming.id)) == 1
    db_session.commit()
    [snapshot] = _on(published, admin_topic_channel("homework"))
    assert snapshot["data"]["progress"]["overall_status"] == "done"


def test_recompute_progress_past_date_no_notify(
    db_session: Session, clock: FakeClock, kick_off: None
) -> None:
    ming = _with_parent(db_session)
    past = date(2026, 8, 31)
    make_homework_item(db_session, ming, service_date=past, status="done")

    change = recompute_progress(db_session, ming.id, past, clock=clock)

    assert change.new_status == "done"
    assert _notifications(db_session, "homework.done", ming.id) == []


def test_recompute_progress_overrides_manual_done(
    db_session: Session, clock: FakeClock, kick_off: None
) -> None:
    ming = make_student(db_session)
    make_homework_progress(db_session, ming, service_date=_DAY, overall_status="done")
    make_homework_item(db_session, ming, service_date=_DAY, status="done")
    make_homework_item(db_session, ming, service_date=_DAY, title="新加的作業")

    change = recompute_progress(db_session, ming.id, _DAY, clock=clock)

    assert (change.old_status, change.new_status) == ("done", "in_progress")
    assert _progress_row(db_session, ming.id).overall_status == "in_progress"


@pytest.fixture
def owner_cleanup_people() -> Iterator[list[tuple[str, UUID]]]:
    """committing 測試建立的監護人 / 學生 / 家長以 owner 連線依 FK 順序刪除；排在
    committing_db_session 之前（先 truncate 作業與通知表，再刪人）。"""
    rows: list[tuple[str, UUID]] = []
    yield rows
    with connect_owner() as conn:
        conn.execute("set lock_timeout = '5s'")
        for table, row_id in rows:
            conn.execute(f"delete from public.{table} where id = %s", (row_id,))  # noqa: S608
        conn.commit()


@pytest.mark.cleanup_tables(
    "homework_items", "homework_daily_progress", "notification_outbox", "notifications"
)
def test_recompute_progress_concurrent_single_notification(
    owner_cleanup_people: list[tuple[str, UUID]],
    committing_db_session: Session,
    db_engine: Engine,
    clock: FakeClock,
    kick_off: None,
) -> None:
    ming = make_student(committing_db_session, name="王小明")
    parent = make_parent(committing_db_session)
    guardian = make_guardian(committing_db_session, ming, parent=parent)
    first = make_homework_item(committing_db_session, ming, service_date=_DAY)
    second = make_homework_item(committing_db_session, ming, service_date=_DAY, title="國語生字")
    committing_db_session.commit()
    owner_cleanup_people.extend(
        [("guardians", guardian.id), ("students", ming.id), ("parent_accounts", parent.id)]
    )

    def finish(session: Session, item_id: UUID) -> ProgressChange:
        item = session.get(HomeworkItem, item_id)
        assert item is not None
        item.status = "done"
        session.flush()
        return recompute_progress(session, ming.id, _DAY, clock=clock)

    s1 = Session(bind=db_engine)
    s2 = Session(bind=db_engine)
    s2_done = threading.Event()
    outcome: dict[str, Any] = {}

    def worker() -> None:
        try:
            outcome["s2"] = finish(s2, second.id)
            s2.commit()
        except BaseException as exc:
            outcome["s2"] = exc
            s2.rollback()
        finally:
            s2_done.set()

    thread = threading.Thread(target=worker)
    try:
        outcome["s1"] = finish(s1, first.id)
        thread.start()
        assert not s2_done.wait(timeout=0.5)  # s1 尚未 commit：s2 在進度列排隊
        s1.commit()
        thread.join(timeout=10)
    finally:
        s1.close()
        if thread.is_alive():
            thread.join(timeout=10)
        s2.close()

    assert outcome["s1"].new_status == "in_progress"
    assert outcome["s2"].new_status == "done"
    assert _progress_row(committing_db_session, ming.id).overall_status == "done"
    assert len(_notifications(committing_db_session, "homework.done", ming.id)) == 1


def test_set_overall_done_without_items(
    db_session: Session, clock: FakeClock, actor: CurrentStaff, kick_off: None
) -> None:
    ming = _with_parent(db_session)

    change = set_overall_status(db_session, ming.id, _DAY, "done", actor=actor, clock=clock)
    again = set_overall_status(db_session, ming.id, _DAY, "done", actor=actor, clock=clock)

    assert (change.old_status, change.new_status) == ("not_started", "done")
    assert (again.old_status, again.new_status) == ("done", "done")
    assert len(_notifications(db_session, "homework.done", ming.id)) == 1


def test_set_overall_auto(
    db_session: Session, clock: FakeClock, actor: CurrentStaff, kick_off: None
) -> None:
    ming = make_student(db_session)
    make_homework_progress(db_session, ming, service_date=_DAY, overall_status="done")
    make_homework_item(db_session, ming, service_date=_DAY, status="done")
    make_homework_item(db_session, ming, service_date=_DAY, title="國語生字")

    change = set_overall_status(db_session, ming.id, _DAY, "auto", actor=actor, clock=clock)

    assert (change.old_status, change.new_status) == ("done", "in_progress")
    assert _progress_row(db_session, ming.id).overall_status == "in_progress"


def test_set_overall_not_found(
    db_session: Session, clock: FakeClock, actor: CurrentStaff, kick_off: None
) -> None:
    ming = make_student(db_session)

    with pytest.raises(AppError) as missing:
        set_overall_status(db_session, uuid4(), _DAY, "done", actor=actor, clock=clock)
    with pytest.raises(AppError) as too_far:
        set_overall_status(db_session, ming.id, date(2026, 9, 9), "done", actor=actor, clock=clock)

    assert (missing.value.status, missing.value.code) == (404, "student_not_found")
    assert (too_far.value.status, too_far.value.code) == (422, "invalid_service_date")


# --- BACKEND-377 create_item ---


def _create_in(student_id: UUID, **overrides: Any) -> HomeworkItemCreateIn:
    fields: dict[str, Any] = {"student_id": student_id, "title": "國語習作 p.5"}
    fields.update(overrides)
    return HomeworkItemCreateIn(**fields)


def _stored_item(db: Session, item_id: UUID) -> HomeworkItem:
    return db.execute(
        select(HomeworkItem)
        .where(HomeworkItem.id == item_id)
        .execution_options(populate_existing=True)
    ).scalar_one()


def _item_count(db: Session, student_id: UUID) -> int:
    return db.execute(
        select(func.count()).select_from(HomeworkItem).where(HomeworkItem.student_id == student_id)
    ).scalar_one()


def _set_window(db: Session, *, past_days: int, future_days: int) -> None:
    db.execute(
        text(
            "update public.system_settings set value = cast(:value as jsonb) "
            "where key = 'homework.window'"
        ),
        {"value": json.dumps({"past_days": past_days, "future_days": future_days})},
    )
    clear_settings_cache()


def test_create_homework_item_success(
    db_session: Session, clock: FakeClock, actor: CurrentStaff, kick_off: None
) -> None:
    ming = make_student(db_session, name="王小明")
    chinese = _subject(db_session, "國語")

    out = create_item(
        db_session, _create_in(ming.id, subject_id=chinese.id), actor=actor, clock=clock
    )

    assert out.item is not None
    assert (out.item.title, out.item.status, out.item.subject_id, out.item.subject_name) == (
        "國語習作 p.5",
        "todo",
        chinese.id,
        "國語",
    )
    assert (out.item.student_id, out.item.service_date, out.item.sort_order) == (ming.id, _DAY, 0)
    assert out.progress.overall_status == "not_started"
    assert (out.progress.student_id, out.progress.service_date) == (ming.id, _DAY)
    assert (out.progress.ready_eta, out.progress.note) == (None, None)
    stored = _stored_item(db_session, out.item.id)
    assert (stored.updated_by, stored.student_id, stored.service_date) == (actor.id, ming.id, _DAY)
    assert _progress_row(db_session, ming.id).overall_status == "not_started"


def test_create_homework_item_passes_status_and_sort_order(
    db_session: Session, clock: FakeClock, actor: CurrentStaff, kick_off: None
) -> None:
    ming = make_student(db_session)

    out = create_item(
        db_session,
        _create_in(ming.id, status="doing", sort_order=7, service_date=date(2026, 9, 2)),
        actor=actor,
        clock=clock,
    )

    assert out.item is not None
    assert (out.item.status, out.item.sort_order, out.item.service_date) == (
        "doing",
        7,
        date(2026, 9, 2),
    )
    assert out.item.subject_id is None
    assert out.item.subject_name is None
    assert (out.progress.service_date, out.progress.overall_status) == (
        date(2026, 9, 2),
        "in_progress",
    )


def test_create_homework_item_resets_done(
    db_session: Session, clock: FakeClock, actor: CurrentStaff, kick_off: None
) -> None:
    ming = _with_parent(db_session)
    make_homework_item(db_session, ming, service_date=_DAY, status="done", title="國語生字")
    make_homework_progress(db_session, ming, service_date=_DAY, overall_status="done")

    out = create_item(db_session, _create_in(ming.id, title="數學習作"), actor=actor, clock=clock)

    assert out.progress.overall_status == "in_progress"
    assert _progress_row(db_session, ming.id).overall_status == "in_progress"
    assert _notifications(db_session, "homework.done", ming.id) == []


def test_create_homework_item_done_notifies_parent_once(
    db_session: Session, clock: FakeClock, actor: CurrentStaff, kick_off: None
) -> None:
    """唯一的項目直接建成 done → 整體轉 done，今天的 done 通知家長一次。"""
    ming = _with_parent(db_session)

    out = create_item(db_session, _create_in(ming.id, status="done"), actor=actor, clock=clock)
    create_item(
        db_session, _create_in(ming.id, status="done", title="數學"), actor=actor, clock=clock
    )

    assert out.progress.overall_status == "done"
    assert len(_notifications(db_session, "homework.done", ming.id)) == 1


def test_create_homework_item_defaults_to_taipei_today(
    db_session: Session, clock: FakeClock, actor: CurrentStaff, kick_off: None
) -> None:
    """跨午夜：UTC 9/1 16:30 已是台北 9/2，沒給 service_date 時建在 9/2。"""
    ming = make_student(db_session)
    clock.set(datetime(2026, 9, 1, 16, 30, tzinfo=UTC))

    out = create_item(db_session, _create_in(ming.id), actor=actor, clock=clock)

    assert out.item is not None
    assert out.item.service_date == date(2026, 9, 2)
    assert out.progress.service_date == date(2026, 9, 2)


def test_create_homework_item_broadcasts_snapshot(
    db_session: Session,
    clock: FakeClock,
    actor: CurrentStaff,
    kick_off: None,
    published: list[Call],
) -> None:
    ming = make_student(db_session)
    make_guardian(db_session, ming, parent=make_parent(db_session))

    out = create_item(db_session, _create_in(ming.id, title="數學習作"), actor=actor, clock=clock)
    assert published == []
    db_session.commit()

    assert out.item is not None
    [admin] = _on(published, admin_topic_channel("homework"))
    assert [i["title"] for i in admin["data"]["items"]] == ["數學習作"]
    assert admin["data"]["items"][0]["id"] == str(out.item.id)
    [parent] = _on(published, student_channel(ming.id))
    assert [i["title"] for i in parent["data"]["items"]] == ["數學習作"]


def test_create_homework_item_validation(
    db_session: Session,
    clock: FakeClock,
    actor: CurrentStaff,
    kick_off: None,
    fresh_settings: None,
) -> None:
    ming = make_student(db_session, name="王小明")
    gone = make_student(db_session, name="已退班")
    gone.status = "withdrawn"
    gone.withdrawn_on = date(2026, 8, 31)
    paused = make_student(db_session, name="暫停中", status="suspended")
    archived = make_student(db_session, name="已封存", archived=True)
    inactive_subject = _subject(db_session, "自然")
    inactive_subject.is_active = False
    db_session.flush()
    _set_window(db_session, past_days=30, future_days=7)

    def fail(data: HomeworkItemCreateIn) -> AppError:
        with pytest.raises(AppError) as excinfo:
            create_item(db_session, data, actor=actor, clock=clock)
        return excinfo.value

    missing = fail(_create_in(uuid4()))
    not_active = fail(_create_in(gone.id))
    suspended = fail(_create_in(paused.id))
    archived_error = fail(_create_in(archived.id))
    stopped_subject = fail(_create_in(ming.id, subject_id=inactive_subject.id))
    unknown_subject = fail(_create_in(ming.id, subject_id=uuid4()))
    too_far = fail(_create_in(ming.id, service_date=_DAY + timedelta(days=8)))

    assert (missing.status, missing.code) == (404, "student_not_found")
    assert (not_active.status, not_active.code) == (409, "student_not_active")
    assert (suspended.status, suspended.code) == (409, "student_not_active")
    assert (archived_error.status, archived_error.code) == (404, "student_not_found")
    assert (stopped_subject.status, stopped_subject.code) == (422, "invalid_subject")
    assert (unknown_subject.status, unknown_subject.code) == (422, "invalid_subject")
    assert (too_far.status, too_far.code) == (422, "invalid_service_date")
    assert too_far.details == {"min_date": "2026-08-02", "max_date": "2026-09-08"}
    # 驗證失敗不留下項目或進度列
    for student in (ming, gone, paused, archived):
        assert _item_count(db_session, student.id) == 0
        assert _progress_count(db_session, student.id) == 0

    # 調高 future_days 並讓快取失效後，同一天可以建立
    _set_window(db_session, past_days=30, future_days=10)
    out = create_item(
        db_session,
        _create_in(ming.id, service_date=_DAY + timedelta(days=8)),
        actor=actor,
        clock=clock,
    )
    assert out.item is not None
    assert out.item.service_date == _DAY + timedelta(days=8)


def test_create_homework_item_window_edges(
    db_session: Session,
    clock: FakeClock,
    actor: CurrentStaff,
    kick_off: None,
    fresh_settings: None,
) -> None:
    """預設 30 / 7：今天 - 30 與今天 + 7 可以，再多一天不行。"""
    ming = make_student(db_session)
    _set_window(db_session, past_days=30, future_days=7)

    oldest = create_item(
        db_session,
        _create_in(ming.id, service_date=_DAY - timedelta(days=30)),
        actor=actor,
        clock=clock,
    )
    latest = create_item(
        db_session,
        _create_in(ming.id, service_date=_DAY + timedelta(days=7)),
        actor=actor,
        clock=clock,
    )
    with pytest.raises(AppError) as too_old:
        create_item(
            db_session,
            _create_in(ming.id, service_date=_DAY - timedelta(days=31)),
            actor=actor,
            clock=clock,
        )

    assert oldest.item is not None
    assert oldest.item.service_date == date(2026, 8, 2)
    assert latest.item is not None
    assert latest.item.service_date == date(2026, 9, 8)
    assert (too_old.value.status, too_old.value.code) == (422, "invalid_service_date")


@pytest.mark.cleanup_tables(
    "homework_items", "homework_daily_progress", "notification_outbox", "notifications"
)
def test_create_homework_item_holds_progress_lock_until_commit(
    owner_cleanup_people: list[tuple[str, UUID]],
    committing_db_session: Session,
    db_engine: Engine,
    clock: FakeClock,
    kick_off: None,
) -> None:
    """新增項目的交易在 commit 前一直持有進度列鎖。

    另一位老師同時把最後一個未完成項目改成 done：他必須排在後面，等新增者 commit 後才看得到
    新的 todo 項目，所以整體仍是 in_progress、不會誤發 homework.done。拿掉進度列鎖時他不用等，
    只看到全部 done 而寫成 done（過期結果）。
    """
    ming = make_student(committing_db_session, name="王小明")
    parent = make_parent(committing_db_session)
    guardian = make_guardian(committing_db_session, ming, parent=parent)
    staff = make_staff(committing_db_session, role_code="tutor")
    make_homework_item(committing_db_session, ming, service_date=_DAY, status="done")
    open_item = make_homework_item(
        committing_db_session, ming, service_date=_DAY, title="數學習作", status="doing"
    )
    make_homework_progress(
        committing_db_session, ming, service_date=_DAY, overall_status="in_progress"
    )
    committing_db_session.commit()
    owner_cleanup_people.extend(
        [
            ("guardians", guardian.id),
            ("students", ming.id),
            ("parent_accounts", parent.id),
            ("staff_users", staff.id),
        ]
    )
    creator = _current(staff)

    s1 = Session(bind=db_engine)
    s2 = Session(bind=db_engine)
    s2_done = threading.Event()
    outcome: dict[str, Any] = {}

    def finish_open_item() -> None:
        try:
            s2.execute(text("set local lock_timeout = '10s'"))
            item = s2.get(HomeworkItem, open_item.id)
            assert item is not None
            item.status = "done"
            s2.flush()
            outcome["s2"] = recompute_progress(s2, ming.id, _DAY, clock=clock)
            s2.commit()
        except BaseException as exc:
            outcome["s2"] = exc
            s2.rollback()
        finally:
            s2_done.set()

    thread = threading.Thread(target=finish_open_item)
    try:
        s1.execute(text("set local lock_timeout = '10s'"))
        outcome["s1"] = create_item(
            s1, _create_in(ming.id, title="國語第5課生字"), actor=creator, clock=clock
        )
        thread.start()
        assert not s2_done.wait(timeout=0.5)  # s1 尚未 commit：s2 在進度列排隊
        s1.commit()
        thread.join(timeout=10)
    finally:
        s1.close()
        if thread.is_alive():
            thread.join(timeout=10)
        s2.close()

    assert outcome["s1"].progress.overall_status == "in_progress"
    change = outcome["s2"]
    assert isinstance(change, ProgressChange)
    assert (change.old_status, change.new_status) == ("in_progress", "in_progress")
    assert _progress_row(committing_db_session, ming.id).overall_status == "in_progress"
    assert _item_count(committing_db_session, ming.id) == 3
    assert _notifications(committing_db_session, "homework.done", ming.id) == []


# --- BACKEND-378 batch_create_items ---


def _batch_in(class_id: UUID, **overrides: Any) -> HomeworkBatchCreateIn:
    fields: dict[str, Any] = {"class_id": class_id, "title": "國語第5課生字"}
    fields.update(overrides)
    return HomeworkBatchCreateIn(**fields)


def _titles(db: Session, student_id: UUID, service_date: date = _DAY) -> list[str]:
    return list(
        db.execute(
            select(HomeworkItem.title)
            .where(HomeworkItem.student_id == student_id, HomeworkItem.service_date == service_date)
            .order_by(HomeworkItem.sort_order, HomeworkItem.id)
        ).scalars()
    )


def test_batch_create_items_whole_class(
    db_session: Session, clock: FakeClock, actor: CurrentStaff, kick_off: None
) -> None:
    class_a = make_class(db_session, name="A班")
    class_b = make_class(db_session, name="B班")
    active = [
        make_student(db_session, name=name, class_=class_a)
        for name in ("王小明", "陳小華", "林小安")
    ]
    paused = make_student(db_session, name="暫停中", class_=class_a, status="suspended")
    archived = make_student(db_session, name="已封存", class_=class_a, archived=True)
    gone = make_student(db_session, name="已退班", class_=class_a)
    gone.status = "withdrawn"
    gone.withdrawn_on = date(2026, 8, 31)
    other_class = make_student(db_session, name="隔壁班", class_=class_b)
    chinese = _subject(db_session, "國語")
    db_session.flush()

    out = batch_create_items(
        db_session, _batch_in(class_a.id, subject_id=chinese.id), actor=actor, clock=clock
    )

    assert out.created == 3
    assert {item.student_id for item in out.items} == {s.id for s in active}
    assert len(out.items) == 3
    for item in out.items:
        assert (item.title, item.status, item.subject_name, item.service_date) == (
            "國語第5課生字",
            "todo",
            "國語",
            _DAY,
        )
        assert item.sort_order == 0
    for student in active:
        assert _titles(db_session, student.id) == ["國語第5課生字"]
        assert _progress_row(db_session, student.id).overall_status == "not_started"
    for excluded in (paused, archived, gone, other_class):
        assert _item_count(db_session, excluded.id) == 0
        assert _progress_count(db_session, excluded.id) == 0
    stored = _stored_item(db_session, out.items[0].id)
    assert stored.updated_by == actor.id


def test_batch_create_items_subset_and_validation(
    db_session: Session,
    clock: FakeClock,
    actor: CurrentStaff,
    kick_off: None,
    fresh_settings: None,
) -> None:
    class_a = make_class(db_session, name="A班")
    class_b = make_class(db_session, name="B班")
    ming = make_student(db_session, name="王小明", class_=class_a)
    hua = make_student(db_session, name="陳小華", class_=class_a)
    paused = make_student(db_session, name="暫停中", class_=class_a, status="suspended")
    outsider = make_student(db_session, name="隔壁班", class_=class_b)
    empty_class = make_class(db_session, name="空班")
    archived_class = make_class(db_session, name="舊班", archived=True)
    inactive_subject = _subject(db_session, "自然")
    inactive_subject.is_active = False
    db_session.flush()

    def fail(data: HomeworkBatchCreateIn) -> AppError:
        with pytest.raises(AppError) as excinfo:
            batch_create_items(db_session, data, actor=actor, clock=clock)
        return excinfo.value

    not_in_class = fail(_batch_in(class_a.id, student_ids=[ming.id, outsider.id]))
    not_active = fail(_batch_in(class_a.id, student_ids=[paused.id]))
    unknown_student = fail(_batch_in(class_a.id, student_ids=[uuid4()]))
    no_students = fail(_batch_in(empty_class.id))
    archived = fail(_batch_in(archived_class.id))
    missing_class = fail(_batch_in(uuid4()))
    bad_subject = fail(_batch_in(class_a.id, subject_id=inactive_subject.id))
    unknown_subject = fail(_batch_in(class_a.id, subject_id=uuid4()))
    too_far = fail(_batch_in(class_a.id, service_date=_DAY + timedelta(days=8)))

    assert (not_in_class.status, not_in_class.code) == (422, "student_not_in_class")
    assert not_in_class.details == {"student_ids": [outsider.id]}
    assert (not_active.status, not_active.code) == (422, "student_not_in_class")
    assert not_active.details == {"student_ids": [paused.id]}
    assert unknown_student.code == "student_not_in_class"
    assert (no_students.status, no_students.code) == (422, "no_students")
    assert (archived.status, archived.code) == (404, "class_not_found")
    assert (missing_class.status, missing_class.code) == (404, "class_not_found")
    assert (bad_subject.status, bad_subject.code) == (422, "invalid_subject")
    assert (unknown_subject.status, unknown_subject.code) == (422, "invalid_subject")
    assert (too_far.status, too_far.code) == (422, "invalid_service_date")
    # 驗證失敗（含部分學生合法時）不留下任何項目或進度列
    for student in (ming, hua, paused, outsider):
        assert _item_count(db_session, student.id) == 0
        assert _progress_count(db_session, student.id) == 0

    out = batch_create_items(
        db_session, _batch_in(class_a.id, student_ids=[ming.id]), actor=actor, clock=clock
    )

    assert out.created == 1
    assert [item.student_id for item in out.items] == [ming.id]
    assert _item_count(db_session, ming.id) == 1
    assert _item_count(db_session, hua.id) == 0


def test_batch_create_items_sort_order(
    db_session: Session, clock: FakeClock, actor: CurrentStaff, kick_off: None
) -> None:
    class_a = make_class(db_session)
    ming = make_student(db_session, name="王小明", class_=class_a)
    hua = make_student(db_session, name="陳小華", class_=class_a)
    an = make_student(db_session, name="林小安", class_=class_a)
    make_homework_item(db_session, ming, service_date=_DAY, title="既有 0", sort_order=0)
    make_homework_item(db_session, ming, service_date=_DAY, title="既有 1", sort_order=1)
    make_homework_item(db_session, an, service_date=_DAY, title="既有 5", sort_order=5)
    # 別天的項目不算
    make_homework_item(db_session, hua, service_date=date(2026, 9, 2), title="隔天 9", sort_order=9)
    db_session.flush()

    out = batch_create_items(db_session, _batch_in(class_a.id), actor=actor, clock=clock)

    by_student = {item.student_id: item for item in out.items}
    assert by_student[ming.id].sort_order == 2
    assert by_student[an.id].sort_order == 6
    assert by_student[hua.id].sort_order == 0
    assert _titles(db_session, ming.id) == ["既有 0", "既有 1", "國語第5課生字"]


def test_batch_create_items_lock_order(
    db_session: Session,
    clock: FakeClock,
    actor: CurrentStaff,
    kick_off: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """進度列依 student_id 排序鎖定，與班級內的排列順序（學號）無關。"""
    class_a = make_class(db_session)
    students = [make_student(db_session, name=f"學生{n}", class_=class_a) for n in range(5)]
    # 學號依 id 由大到小編號：班級順序（學號）剛好與 id 順序相反
    for rank, student in enumerate(sorted(students, key=lambda s: s.id, reverse=True)):
        student.student_no = f"LK-{rank:02d}"
    db_session.flush()
    natural_order = list_active_student_ids(db_session, class_id=class_a.id)
    assert natural_order == sorted((s.id for s in students), reverse=True)

    locked: list[UUID] = []
    real_lock = homework_service.lock_progress_row

    def record(session: Session, student_id: UUID, service_date: date) -> HomeworkDailyProgress:
        locked.append(student_id)
        return real_lock(session, student_id, service_date)

    monkeypatch.setattr(homework_service, "lock_progress_row", record)

    out = batch_create_items(db_session, _batch_in(class_a.id), actor=actor, clock=clock)

    assert out.created == 5
    assert locked == sorted(s.id for s in students)


def test_batch_create_items_recomputes_progress_and_broadcasts(
    db_session: Session,
    clock: FakeClock,
    actor: CurrentStaff,
    kick_off: None,
    published: list[Call],
) -> None:
    class_a = make_class(db_session)
    ming = make_student(db_session, name="王小明", class_=class_a)
    hua = make_student(db_session, name="陳小華", class_=class_a)
    make_guardian(db_session, ming, parent=make_parent(db_session))
    make_homework_item(db_session, ming, service_date=_DAY, status="done", title="國語生字")
    make_homework_progress(db_session, ming, service_date=_DAY, overall_status="done")
    db_session.flush()

    batch_create_items(
        db_session, _batch_in(class_a.id, title="數學習作"), actor=actor, clock=clock
    )
    db_session.commit()

    # 全部 done 的學生新增 todo 後回到 in_progress；沒有項目的學生建立 not_started
    assert _progress_row(db_session, ming.id).overall_status == "in_progress"
    assert _progress_row(db_session, hua.id).overall_status == "not_started"
    assert _notifications(db_session, "homework.done", ming.id) == []
    # 每位學生各推一次快照，內容含新項目
    snapshots = _on(published, admin_topic_channel("homework"))
    assert len(snapshots) == 2
    assert {s["data"]["student_id"] for s in snapshots} == {str(ming.id), str(hua.id)}
    for snapshot in snapshots:
        assert "數學習作" in [i["title"] for i in snapshot["data"]["items"]]


def test_batch_create_items_defaults_to_taipei_today(
    db_session: Session, clock: FakeClock, actor: CurrentStaff, kick_off: None
) -> None:
    """跨午夜：UTC 9/1 16:30 已是台北 9/2，沒給 service_date 時建在 9/2。"""
    class_a = make_class(db_session)
    ming = make_student(db_session, class_=class_a)
    clock.set(datetime(2026, 9, 1, 16, 30, tzinfo=UTC))

    out = batch_create_items(db_session, _batch_in(class_a.id), actor=actor, clock=clock)

    assert [item.service_date for item in out.items] == [date(2026, 9, 2)]
    assert _titles(db_session, ming.id, date(2026, 9, 2)) == ["國語第5課生字"]
    assert _titles(db_session, ming.id, _DAY) == []


@pytest.mark.cleanup_tables(
    "homework_items", "homework_daily_progress", "notification_outbox", "notifications"
)
def test_batch_create_items_holds_progress_locks_until_commit(
    owner_cleanup_people: list[tuple[str, UUID]],
    committing_db_session: Session,
    db_engine: Engine,
    clock: FakeClock,
    kick_off: None,
) -> None:
    """批次新增的交易在 commit 前持有每位學生的進度列鎖。

    同時把陳小華最後一個未完成項目改成 done 的老師必須排隊，等批次 commit 後才看得到新的 todo
    項目，所以整體仍是 in_progress、不會誤發 homework.done。
    """
    class_a = make_class(committing_db_session)
    ming = make_student(committing_db_session, name="王小明", class_=class_a)
    hua = make_student(committing_db_session, name="陳小華", class_=class_a)
    parent = make_parent(committing_db_session)
    guardian = make_guardian(committing_db_session, hua, parent=parent)
    staff = make_staff(committing_db_session, role_code="tutor")
    make_homework_item(committing_db_session, hua, service_date=_DAY, status="done")
    open_item = make_homework_item(
        committing_db_session, hua, service_date=_DAY, title="數學習作", status="doing"
    )
    make_homework_progress(
        committing_db_session, hua, service_date=_DAY, overall_status="in_progress"
    )
    committing_db_session.commit()
    owner_cleanup_people.extend(
        [
            ("guardians", guardian.id),
            ("students", ming.id),
            ("students", hua.id),
            ("classes", class_a.id),
            ("parent_accounts", parent.id),
            ("staff_users", staff.id),
        ]
    )
    creator = _current(staff)

    s1 = Session(bind=db_engine)
    s2 = Session(bind=db_engine)
    s2_done = threading.Event()
    outcome: dict[str, Any] = {}

    def finish_open_item() -> None:
        try:
            s2.execute(text("set local lock_timeout = '10s'"))
            item = s2.get(HomeworkItem, open_item.id)
            assert item is not None
            item.status = "done"
            s2.flush()
            outcome["s2"] = recompute_progress(s2, hua.id, _DAY, clock=clock)
            s2.commit()
        except BaseException as exc:
            outcome["s2"] = exc
            s2.rollback()
        finally:
            s2_done.set()

    thread = threading.Thread(target=finish_open_item)
    try:
        s1.execute(text("set local lock_timeout = '10s'"))
        outcome["s1"] = batch_create_items(s1, _batch_in(class_a.id), actor=creator, clock=clock)
        thread.start()
        assert not s2_done.wait(timeout=0.5)  # 批次尚未 commit：s2 在陳小華的進度列排隊
        s1.commit()
        thread.join(timeout=10)
    finally:
        s1.close()
        if thread.is_alive():
            thread.join(timeout=10)
        s2.close()

    assert outcome["s1"].created == 2
    change = outcome["s2"]
    assert isinstance(change, ProgressChange)
    assert (change.old_status, change.new_status) == ("in_progress", "in_progress")
    assert _progress_row(committing_db_session, hua.id).overall_status == "in_progress"
    assert _item_count(committing_db_session, hua.id) == 3
    assert _item_count(committing_db_session, ming.id) == 1
    assert _notifications(committing_db_session, "homework.done", hua.id) == []


# --- BACKEND-379 update_item ---


def _update_in(**fields: Any) -> HomeworkItemUpdateIn:
    return HomeworkItemUpdateIn(**fields)


def test_update_homework_item_status_done(
    db_session: Session, clock: FakeClock, actor: CurrentStaff, kick_off: None
) -> None:
    ming = _with_parent(db_session)
    make_homework_item(db_session, ming, service_date=_DAY, status="done", title="國語生字")
    last = make_homework_item(db_session, ming, service_date=_DAY, status="doing", title="數學習作")
    make_homework_progress(db_session, ming, service_date=_DAY, overall_status="in_progress")

    out = update_item(db_session, last.id, _update_in(status="done"), actor=actor, clock=clock)

    assert out.item is not None
    assert (out.item.id, out.item.status, out.item.title) == (last.id, "done", "數學習作")
    assert out.progress.overall_status == "done"
    assert _progress_row(db_session, ming.id).overall_status == "done"
    [notification] = _notifications(db_session, "homework.done", ming.id)
    assert notification.title == "王小明 作業已完成"
    stored = _stored_item(db_session, last.id)
    assert (stored.status, stored.updated_by) == ("done", actor.id)


def test_update_homework_item_partial(
    db_session: Session, clock: FakeClock, actor: CurrentStaff, kick_off: None
) -> None:
    ming = make_student(db_session, name="王小明")
    math = _subject(db_session, "數學")
    item = make_homework_item(
        db_session,
        ming,
        service_date=_DAY,
        title="數學習作",
        status="doing",
        subject=math,
        sort_order=3,
    )

    renamed = update_item(
        db_session, item.id, _update_in(title="數學習作 p.12-13"), actor=actor, clock=clock
    )

    assert renamed.item is not None
    assert (
        renamed.item.title,
        renamed.item.status,
        renamed.item.subject_id,
        renamed.item.subject_name,
        renamed.item.sort_order,
    ) == ("數學習作 p.12-13", "doing", math.id, "數學", 3)
    assert renamed.progress.overall_status == "in_progress"
    stored = _stored_item(db_session, item.id)
    assert (stored.title, stored.status, stored.subject_id, stored.sort_order) == (
        "數學習作 p.12-13",
        "doing",
        math.id,
        3,
    )
    assert stored.updated_by == actor.id

    reordered = update_item(db_session, item.id, _update_in(sort_order=9), actor=actor, clock=clock)
    corrected = update_item(
        db_session, item.id, _update_in(status="correcting"), actor=actor, clock=clock
    )

    assert reordered.item is not None
    assert (reordered.item.sort_order, reordered.item.status, reordered.item.title) == (
        9,
        "doing",
        "數學習作 p.12-13",
    )
    assert corrected.item is not None
    assert (corrected.item.status, corrected.item.sort_order) == ("correcting", 9)
    assert corrected.progress.overall_status == "in_progress"


def test_update_homework_item_subject_set_and_clear(
    db_session: Session, clock: FakeClock, actor: CurrentStaff, kick_off: None
) -> None:
    ming = make_student(db_session)
    math = _subject(db_session, "數學")
    chinese = _subject(db_session, "國語")
    item = make_homework_item(db_session, ming, service_date=_DAY, subject=math)

    changed = update_item(
        db_session, item.id, _update_in(subject_id=chinese.id), actor=actor, clock=clock
    )
    cleared = update_item(
        db_session, item.id, _update_in(subject_id=None), actor=actor, clock=clock
    )

    assert changed.item is not None
    assert (changed.item.subject_id, changed.item.subject_name) == (chinese.id, "國語")
    assert cleared.item is not None
    assert (cleared.item.subject_id, cleared.item.subject_name) == (None, None)
    assert _stored_item(db_session, item.id).subject_id is None


def test_update_homework_item_errors(
    db_session: Session, clock: FakeClock, actor: CurrentStaff, kick_off: None
) -> None:
    ming = make_student(db_session)
    item = make_homework_item(db_session, ming, service_date=_DAY, title="國語生字")
    inactive_subject = _subject(db_session, "自然")
    inactive_subject.is_active = False
    db_session.flush()

    def fail(item_id: UUID, **fields: Any) -> AppError:
        with pytest.raises(AppError) as excinfo:
            update_item(db_session, item_id, _update_in(**fields), actor=actor, clock=clock)
        return excinfo.value

    missing = fail(uuid4(), title="x")
    unknown_subject = fail(item.id, title="改名", subject_id=uuid4())
    stopped_subject = fail(item.id, subject_id=inactive_subject.id)

    assert (missing.status, missing.code) == (404, "homework_item_not_found")
    assert (unknown_subject.status, unknown_subject.code) == (422, "invalid_subject")
    assert (stopped_subject.status, stopped_subject.code) == (422, "invalid_subject")
    # 驗證失敗時整筆都不改（含同一請求裡本來合法的 title）
    stored = _stored_item(db_session, item.id)
    assert (stored.title, stored.subject_id, stored.updated_by) == ("國語生字", None, None)


def test_update_homework_item_reads_current_row_after_locking(
    db_session: Session, clock: FakeClock, actor: CurrentStaff, kick_off: None
) -> None:
    """同一 session 已載入過該項目、DB 裡的值之後被別人改掉：鎖列後讀到的是 DB 現值。"""
    ming = make_student(db_session)
    item = make_homework_item(db_session, ming, service_date=_DAY, status="doing", title="國語生字")
    db_session.flush()
    assert item.status == "doing"  # identity map 內是 doing
    db_session.execute(
        update(HomeworkItem)
        .where(HomeworkItem.id == item.id)
        .values(status="done", title="別人改的標題"),
        execution_options={"synchronize_session": False},
    )

    out = update_item(db_session, item.id, _update_in(sort_order=4), actor=actor, clock=clock)

    assert out.item is not None
    assert (out.item.status, out.item.title, out.item.sort_order) == ("done", "別人改的標題", 4)
    assert out.progress.overall_status == "done"


def test_update_homework_item_back_to_open_resets_done(
    db_session: Session, clock: FakeClock, actor: CurrentStaff, kick_off: None
) -> None:
    ming = _with_parent(db_session)
    make_homework_item(db_session, ming, service_date=_DAY, status="done", title="國語生字")
    second = make_homework_item(db_session, ming, service_date=_DAY, status="done", title="數學")
    make_homework_progress(db_session, ming, service_date=_DAY, overall_status="done")

    out = update_item(
        db_session, second.id, _update_in(status="correcting"), actor=actor, clock=clock
    )

    assert out.progress.overall_status == "in_progress"
    assert _progress_row(db_session, ming.id).overall_status == "in_progress"
    assert _notifications(db_session, "homework.done", ming.id) == []


def test_update_homework_item_notifies_only_when_turning_done_today(
    db_session: Session, clock: FakeClock, actor: CurrentStaff, kick_off: None
) -> None:
    ming = _with_parent(db_session)
    today_item = make_homework_item(db_session, ming, service_date=_DAY, status="doing")
    tomorrow_item = make_homework_item(
        db_session, ming, service_date=date(2026, 9, 2), status="doing", title="明天的作業"
    )

    update_item(db_session, today_item.id, _update_in(status="done"), actor=actor, clock=clock)
    update_item(db_session, today_item.id, _update_in(title="改名"), actor=actor, clock=clock)
    update_item(db_session, today_item.id, _update_in(status="done"), actor=actor, clock=clock)
    tomorrow = update_item(
        db_session, tomorrow_item.id, _update_in(status="done"), actor=actor, clock=clock
    )

    # 今天轉 done 通知一次；重複存成 done 不再通知；別天的作業轉 done 不通知
    assert tomorrow.progress.overall_status == "done"
    assert len(_notifications(db_session, "homework.done", ming.id)) == 1


def test_update_homework_item_title_only_still_publishes_snapshot(
    db_session: Session,
    clock: FakeClock,
    actor: CurrentStaff,
    kick_off: None,
    published: list[Call],
) -> None:
    ming = make_student(db_session)
    make_guardian(db_session, ming, parent=make_parent(db_session))
    item = make_homework_item(db_session, ming, service_date=_DAY, status="doing", title="數學")

    update_item(db_session, item.id, _update_in(title="數學習作"), actor=actor, clock=clock)
    db_session.commit()

    [admin] = _on(published, admin_topic_channel("homework"))
    assert [(i["title"], i["status"]) for i in admin["data"]["items"]] == [("數學習作", "doing")]
    [parent] = _on(published, student_channel(ming.id))
    assert [i["title"] for i in parent["data"]["items"]] == ["數學習作"]


@pytest.mark.cleanup_tables(
    "homework_items", "homework_daily_progress", "notification_outbox", "notifications"
)
def test_update_homework_item_concurrent_updates_single_notification(
    owner_cleanup_people: list[tuple[str, UUID]],
    committing_db_session: Session,
    db_engine: Engine,
    clock: FakeClock,
    kick_off: None,
) -> None:
    """兩位老師同時把同一學生最後兩個項目改成 done：第二位排在進度列後面，看到第一位的結果後
    才重算，所以整體最終是 done 且 homework.done 恰好一則。拿掉進度列鎖時兩人互相看不到對方
    未 commit 的 done，各自算出 in_progress，最終項目全 done 但整體停在 in_progress、沒有通知。"""
    ming = make_student(committing_db_session, name="王小明")
    parent = make_parent(committing_db_session)
    guardian = make_guardian(committing_db_session, ming, parent=parent)
    staff = make_staff(committing_db_session, role_code="tutor")
    first = make_homework_item(committing_db_session, ming, service_date=_DAY, status="doing")
    second = make_homework_item(
        committing_db_session, ming, service_date=_DAY, title="國語生字", status="doing"
    )
    make_homework_progress(
        committing_db_session, ming, service_date=_DAY, overall_status="in_progress"
    )
    committing_db_session.commit()
    owner_cleanup_people.extend(
        [
            ("guardians", guardian.id),
            ("students", ming.id),
            ("parent_accounts", parent.id),
            ("staff_users", staff.id),
        ]
    )
    teacher = _current(staff)

    s1 = Session(bind=db_engine)
    s2 = Session(bind=db_engine)
    s2_done = threading.Event()
    outcome: dict[str, Any] = {}

    def finish_second() -> None:
        try:
            s2.execute(text("set local lock_timeout = '10s'"))
            outcome["s2"] = update_item(
                s2, second.id, _update_in(status="done"), actor=teacher, clock=clock
            )
            s2.commit()
        except BaseException as exc:
            outcome["s2"] = exc
            s2.rollback()
        finally:
            s2_done.set()

    thread = threading.Thread(target=finish_second)
    try:
        s1.execute(text("set local lock_timeout = '10s'"))
        outcome["s1"] = update_item(
            s1, first.id, _update_in(status="done"), actor=teacher, clock=clock
        )
        thread.start()
        assert not s2_done.wait(timeout=0.5)  # s1 尚未 commit：s2 在進度列排隊
        s1.commit()
        thread.join(timeout=10)
    finally:
        s1.close()
        if thread.is_alive():
            thread.join(timeout=10)
        s2.close()

    assert outcome["s1"].progress.overall_status == "in_progress"
    assert outcome["s2"].progress.overall_status == "done"
    assert _progress_row(committing_db_session, ming.id).overall_status == "done"
    assert len(_notifications(committing_db_session, "homework.done", ming.id)) == 1


@pytest.mark.cleanup_tables(
    "homework_items", "homework_daily_progress", "notification_outbox", "notifications"
)
def test_update_homework_item_waits_for_concurrent_delete_then_404(
    owner_cleanup_people: list[tuple[str, UUID]],
    committing_db_session: Session,
    db_engine: Engine,
    clock: FakeClock,
    kick_off: None,
) -> None:
    """項目正在被另一個交易刪除時，修改者鎖項目列排隊，刪除 commit 後回 404，而不是在 UPDATE
    時撞上 0 列（StaleDataError → 500）。"""
    ming = make_student(committing_db_session, name="王小明")
    staff = make_staff(committing_db_session, role_code="tutor")
    item = make_homework_item(committing_db_session, ming, service_date=_DAY, status="doing")
    committing_db_session.commit()
    owner_cleanup_people.extend([("students", ming.id), ("staff_users", staff.id)])
    teacher = _current(staff)
    item_id = item.id

    s1 = Session(bind=db_engine)
    s2 = Session(bind=db_engine)
    s2_done = threading.Event()
    outcome: dict[str, Any] = {}

    def rename() -> None:
        try:
            s2.execute(text("set local lock_timeout = '10s'"))
            outcome["s2"] = update_item(
                s2, item_id, _update_in(title="改名"), actor=teacher, clock=clock
            )
            s2.commit()
        except BaseException as exc:
            outcome["s2"] = exc
            s2.rollback()
        finally:
            s2_done.set()

    thread = threading.Thread(target=rename)
    try:
        s1.execute(text("set local lock_timeout = '10s'"))
        doomed = s1.get(HomeworkItem, item_id)
        assert doomed is not None
        s1.delete(doomed)
        s1.flush()
        thread.start()
        assert not s2_done.wait(timeout=0.5)  # 刪除尚未 commit：修改者排隊
        s1.commit()
        thread.join(timeout=10)
    finally:
        s1.close()
        if thread.is_alive():
            thread.join(timeout=10)
        s2.close()

    error = outcome["s2"]
    assert isinstance(error, AppError), repr(error)
    assert (error.status, error.code) == (404, "homework_item_not_found")
    assert _item_count(committing_db_session, ming.id) == 0
