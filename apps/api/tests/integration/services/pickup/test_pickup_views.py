"""BACKEND-404：app/services/pickup/views.py（接送請求回應組裝與 ws 推播）。

推播測試以 monkeypatch 記錄 publish_threadsafe；commit 走 db_session（savepoint 模式的 commit
同樣觸發 before_commit / after_commit，見 BACKEND-006）。
"""

from collections.abc import Iterator
from datetime import date, time
from typing import Any

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.core.clock import combine_taipei
from app.core.config import get_settings
from app.core.crypto import derive_key
from app.core.tx_hooks import install_tx_hooks
from app.models.pickup import PickupRequest
from app.realtime import publish as publish_module
from app.realtime.publish import admin_topic_channel, student_channel
from app.services.pickup.views import (
    build_parent_request_views,
    build_request_views,
    publish_request_change,
)
from tests.support.factories import (
    ARCHIVED_AT,
    make_class,
    make_guardian,
    make_homework_progress,
    make_parent,
    make_pickup_authorization,
    make_pickup_request,
    make_staff,
    make_student,
)
from tests.support.fake_clock import FakeClock

_DAY = date(2026, 9, 1)
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
    yield
    get_settings.cache_clear()
    derive_key.cache_clear()


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


def _complete(
    session: Session,
    request: PickupRequest,
    *,
    method: str,
    guardian_id: Any = None,
    authorization_id: Any = None,
    completed_by: Any = None,
) -> None:
    """factory 的 completed 預設為 override；改成指定的完成方式與接走人。"""
    request.completion_method = method  # type: ignore[assignment]
    request.picked_up_by_guardian_id = guardian_id
    request.picked_up_by_authorization_id = authorization_id
    request.completed_by = completed_by
    session.flush()


def test_pickup_views_names(db_session: Session) -> None:
    class_a = make_class(db_session, name="A班")
    ming = make_student(db_session, name="王小明", class_=class_a)
    mom = make_parent(db_session, display_name="LINE 暱稱")
    make_guardian(db_session, ming, parent=mom, name="王媽媽")
    dad = make_guardian(db_session, ming, name="王爸爸", relation="father")
    counter = make_staff(db_session, display_name="陳主任")
    replied = make_pickup_request(
        db_session,
        ming,
        service_date=_DAY,
        status="acknowledged",
        requested_by=mom.id,
        reply_source="staff",
        reply_message="17:30 好",
        reply_ready_eta=time(17, 30),
        expected_arrival_at=combine_taipei(_DAY, time(17, 45)),
    )
    picked = make_pickup_request(
        db_session, ming, service_date=date(2026, 8, 31), status="completed", requested_by=mom.id
    )
    _complete(db_session, picked, method="guardian", guardian_id=dad.id, completed_by=counter.id)

    replied_out, picked_out = build_request_views(db_session, [replied, picked])

    assert replied_out.id == replied.id
    assert (replied_out.student.name, replied_out.student.class_name) == ("王小明", "A班")
    assert replied_out.requested_by_name == "王媽媽"
    assert replied_out.replied_by_name == "林老師"
    assert (replied_out.expected_arrival_at, replied_out.reply_ready_eta) == ("17:45", "17:30")
    assert picked_out.picked_up_by_name == "王爸爸"
    assert picked_out.completed_by_name == "陳主任"
    assert picked_out.completion_method == "guardian"


def test_pickup_views_other_names(db_session: Session) -> None:
    ming = make_student(db_session, name="王小明")
    hua = make_student(db_session, name="陳小華")
    an = make_student(db_session, name="林小安")
    stranger = make_parent(db_session, display_name="陳阿姨 LINE")  # 不是該生的監護人
    teacher = make_staff(db_session, display_name="張老師")
    by_staff = make_pickup_request(
        db_session, ming, service_date=_DAY, source="staff", requested_by=teacher.id
    )
    by_unlinked = make_pickup_request(db_session, hua, service_date=_DAY, requested_by=stranger.id)
    proxy = make_pickup_request(db_session, an, service_date=_DAY, status="completed")
    auth = make_pickup_authorization(db_session, an, service_date=_DAY, proxy_name="李阿姨")
    _complete(db_session, proxy, method="code", authorization_id=auth.id)
    override = make_pickup_request(
        db_session, ming, service_date=date(2026, 8, 31), status="completed"
    )

    outs = build_request_views(db_session, [by_staff, by_unlinked, proxy, override])

    assert [o.requested_by_name for o in outs[:2]] == ["張老師", "陳阿姨 LINE"]
    assert outs[2].picked_up_by_name == "李阿姨"
    assert outs[3].completion_method == "override"
    assert outs[3].picked_up_by_name == "老師確認交付"
    assert outs[0].picked_up_by_name is None
    assert build_request_views(db_session, []) == []


def test_pickup_views_current_homework(db_session: Session) -> None:
    ming = make_student(db_session)
    hua = make_student(db_session, name="陳小華")
    make_homework_progress(
        db_session, ming, service_date=_DAY, overall_status="in_progress", ready_eta=time(17, 0)
    )
    # 別天的進度不算
    make_homework_progress(db_session, hua, service_date=date(2026, 8, 31), overall_status="done")
    with_progress = make_pickup_request(db_session, ming, service_date=_DAY)
    without = make_pickup_request(db_session, hua, service_date=_DAY)

    first, second = build_request_views(db_session, [with_progress, without])

    assert (first.current_homework_status, first.current_ready_eta) == ("in_progress", "17:00")
    assert (second.current_homework_status, second.current_ready_eta) == (None, None)


def test_pickup_views_flags(db_session: Session) -> None:
    students = [make_student(db_session) for _ in range(6)]
    pending = make_pickup_request(db_session, students[0], service_date=_DAY)
    arrived = make_pickup_request(
        db_session, students[1], service_date=_DAY, status="arrived", reply_source="staff"
    )
    completed = make_pickup_request(db_session, students[2], service_date=_DAY, status="completed")
    auto_no_eta = make_pickup_request(
        db_session, students[3], service_date=_DAY, status="acknowledged", reply_source="auto"
    )
    auto_eta = make_pickup_request(
        db_session,
        students[4],
        service_date=_DAY,
        status="acknowledged",
        reply_source="auto",
        reply_ready_eta=time(17, 30),
    )
    cancelled = make_pickup_request(db_session, students[5], service_date=_DAY, status="cancelled")
    requests = [pending, arrived, completed, auto_no_eta, auto_eta, cancelled]

    admin = build_request_views(db_session, requests)
    parent = build_parent_request_views(db_session, requests)

    assert [o.needs_reply for o in admin] == [True, False, False, True, False, False]
    assert [(o.can_cancel, o.can_mark_arrived) for o in parent] == [
        (True, True),
        (True, False),
        (False, False),
        (True, True),
        (True, True),
        (False, False),
    ]


def test_pickup_views_query_count(db_session: Session) -> None:
    requests: list[PickupRequest] = []
    for index in range(20):
        student = make_student(db_session, name=f"學生{index}")
        parent = make_parent(db_session)
        make_guardian(db_session, student, parent=parent, name=f"家長{index}")
        make_homework_progress(db_session, student, service_date=_DAY)
        request = make_pickup_request(
            db_session,
            student,
            service_date=_DAY,
            status="completed" if index % 2 else "acknowledged",
            requested_by=parent.id,
            reply_source="staff" if index % 2 == 0 else None,
        )
        if index % 4 == 1:
            auth = make_pickup_authorization(db_session, student, service_date=_DAY)
            _complete(db_session, request, method="code", authorization_id=auth.id)
        requests.append(request)
    statements: list[str] = []

    def record(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        statements.append(statement)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", record)
    try:
        outs = build_request_views(db_session, requests)
    finally:
        event.remove(engine, "before_cursor_execute", record)

    assert [o.requested_by_name for o in outs] == [f"家長{i}" for i in range(20)]
    assert outs[1].picked_up_by_name == "李阿姨"
    assert len(statements) <= 6


def test_pickup_views_publish_split(db_session: Session, published: list[Call]) -> None:
    clock = FakeClock(combine_taipei(_DAY, time(16, 0)))
    ming = make_student(db_session, name="王小明")
    teacher = make_staff(db_session, display_name="林老師")
    request = make_pickup_request(db_session, ming, service_date=_DAY)

    publish_request_change(db_session, request, clock=clock)
    request.status = "acknowledged"
    request.reply_source = "staff"
    request.replied_by = teacher.id
    request.replied_at = ARCHIVED_AT
    db_session.flush()
    publish_request_change(db_session, request, clock=clock)
    assert published == []
    db_session.commit()

    [admin] = _on(published, admin_topic_channel("pickup"))
    [parent] = _on(published, student_channel(ming.id))
    assert admin["type"] == parent["type"] == "pickup.request_updated"
    # 推送的是最後狀態
    assert (admin["data"]["status"], admin["data"]["replied_by_name"]) == ("acknowledged", "林老師")
    assert admin["data"]["student"]["name"] == "王小明"
    assert parent["data"]["status"] == "acknowledged"
    assert parent["data"]["student_name"] == "王小明"
    assert "replied_by_name" not in parent["data"]
    assert "completed_by_name" not in parent["data"]


def test_pickup_views_publish_rollback(db_session: Session, published: list[Call]) -> None:
    clock = FakeClock(combine_taipei(_DAY, time(16, 0)))
    request = make_pickup_request(db_session, make_student(db_session), service_date=_DAY)
    db_session.commit()

    publish_request_change(db_session, request, clock=clock)
    db_session.rollback()
    db_session.commit()

    assert published == []


def test_pickup_views_parent_reply_source(db_session: Session) -> None:
    ming, hua = make_student(db_session, name="王小明"), make_student(db_session, name="陳小華")
    by_staff = make_pickup_request(
        db_session, ming, service_date=_DAY, status="acknowledged", reply_source="staff"
    )
    by_auto = make_pickup_request(
        db_session, hua, service_date=_DAY, status="acknowledged", reply_source="auto"
    )

    staff_out, auto_out = build_parent_request_views(db_session, [by_staff, by_auto])

    assert (staff_out.reply_source, staff_out.student_name) == ("staff", "王小明")
    assert "replied_by_name" not in staff_out.model_dump()
    assert auto_out.reply_source == "auto"
