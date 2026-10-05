"""BACKEND-373 / 384 / 374：homework_service（進度列鎖定、家長端當日作業明細、ws 快照推播）。

推播測試以 monkeypatch 記錄 publish_threadsafe；commit 走 db_session（savepoint 模式的 commit
同樣觸發 before_commit / after_commit，見 BACKEND-006）。
"""

import threading
from collections.abc import Iterator
from datetime import UTC, date, datetime, time
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import Engine, func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.tx_hooks import install_tx_hooks
from app.models.homework import HomeworkDailyProgress
from app.models.reference import Subject
from app.realtime import publish as publish_module
from app.realtime.publish import admin_topic_channel, student_channel
from app.services.homework_service import (
    broadcast_homework_snapshot,
    get_child_homework,
    lock_progress_row,
)
from tests.integration.db.conftest import connect_owner
from tests.support.factories import (
    make_homework_item,
    make_homework_progress,
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
