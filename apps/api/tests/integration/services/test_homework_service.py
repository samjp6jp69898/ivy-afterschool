"""BACKEND-373 / 384 / 374 / 383 / 376 / 382：homework_service（進度列鎖定、家長端當日作業明細、
ws 快照推播、作業進度看板、整體完成副作用、設定預計可接送時間、重算整體進度、手動標整體完成）。

推播測試以 monkeypatch 記錄 publish_threadsafe；commit 走 db_session（savepoint 模式的 commit
同樣觸發 before_commit / after_commit，見 BACKEND-006）。
"""

import threading
from collections.abc import Iterator
from datetime import UTC, date, datetime, time
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, event, func, select, text
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
from app.schemas.homework import BoardOut, BoardQuery, BoardStudentOut
from app.services.homework_service import (
    ProgressChange,
    broadcast_homework_snapshot,
    get_board,
    get_child_homework,
    handle_homework_done,
    lock_progress_row,
    recompute_progress,
    set_overall_status,
    set_ready_eta_and_note,
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
