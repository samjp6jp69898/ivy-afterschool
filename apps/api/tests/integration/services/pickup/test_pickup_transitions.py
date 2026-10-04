"""BACKEND-405：app/services/pickup/transitions.py（transition_request，接送請求條件式狀態轉換）。

單一條件式 UPDATE：合法前置狀態才命中；0 列再查原因（404 不存在 / 越權、409 狀態不符）；兩條連線
並發只有一個命中。
"""

import threading
from collections.abc import Iterator
from datetime import date, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models.pickup import OPEN_STATUSES, PickupRequest
from app.services.pickup.transitions import transition_request
from tests.integration.db.conftest import connect_owner
from tests.support.factories import make_pickup_request, make_staff, make_student
from tests.support.fake_clock import FakeClock

_DATE = date(2026, 9, 1)


def _reload(session: Session, request_id: UUID) -> PickupRequest:
    session.expire_all()
    return session.execute(select(PickupRequest).where(PickupRequest.id == request_id)).scalar_one()


def test_transition_request_success(db_session: Session, fake_clock: FakeClock) -> None:
    student = make_student(db_session)
    request = make_pickup_request(db_session, student, service_date=_DATE)

    updated = transition_request(
        db_session, request.id, from_statuses={"pending"}, to_status="acknowledged", values={}
    )
    assert updated.id == request.id
    assert updated.status == "acknowledged"
    assert _reload(db_session, request.id).status == "acknowledged"

    only_fields = transition_request(
        db_session,
        request.id,
        from_statuses=OPEN_STATUSES,
        to_status=None,
        values={"reply_message": "x"},
    )
    assert only_fields.status == "acknowledged"
    assert only_fields.reply_message == "x"

    # values 可帶 SQL 表達式；student_ids 範圍內可操作
    now = fake_clock.now()
    arrived = transition_request(
        db_session,
        request.id,
        from_statuses={"pending", "acknowledged"},
        to_status="arrived",
        values={"arrived_at": func.coalesce(PickupRequest.arrived_at, now)},
        student_ids=[student.id],
    )
    assert arrived.status == "arrived"
    assert arrived.arrived_at == now
    later = transition_request(
        db_session,
        request.id,
        from_statuses={"arrived"},
        to_status=None,
        values={"arrived_at": func.coalesce(PickupRequest.arrived_at, now + timedelta(hours=1))},
    )
    assert later.arrived_at == now


def test_transition_request_invalid_status(db_session: Session) -> None:
    student = make_student(db_session)
    request = make_pickup_request(db_session, student, service_date=_DATE, status="completed")

    with pytest.raises(AppError) as excinfo:
        transition_request(
            db_session, request.id, from_statuses=OPEN_STATUSES, to_status="cancelled", values={}
        )

    assert excinfo.value.status == 409
    assert excinfo.value.code == "invalid_pickup_status"
    assert excinfo.value.details == {"current_status": "completed"}
    assert _reload(db_session, request.id).status == "completed"


def test_transition_request_not_found_and_scope(db_session: Session) -> None:
    student = make_student(db_session)
    other = make_student(db_session)
    request = make_pickup_request(db_session, student, service_date=_DATE)

    with pytest.raises(AppError) as missing:
        transition_request(
            db_session, uuid4(), from_statuses={"pending"}, to_status="acknowledged", values={}
        )
    with pytest.raises(AppError) as foreign:
        transition_request(
            db_session,
            request.id,
            from_statuses={"pending"},
            to_status="acknowledged",
            values={},
            student_ids=[other.id],
        )
    with pytest.raises(AppError) as empty_scope:
        transition_request(
            db_session,
            request.id,
            from_statuses={"pending"},
            to_status="acknowledged",
            values={},
            student_ids=[],
        )

    for excinfo in (missing, foreign, empty_scope):
        assert excinfo.value.status == 404
        assert excinfo.value.code == "pickup_request_not_found"
        assert excinfo.value.message == missing.value.message
        assert excinfo.value.details is None
    # 越權且狀態也不符：仍是 404（不洩漏存在與否）
    done = make_pickup_request(
        db_session, student, service_date=_DATE + timedelta(days=1), status="completed"
    )
    with pytest.raises(AppError) as both:
        transition_request(
            db_session,
            done.id,
            from_statuses={"pending"},
            to_status="acknowledged",
            values={},
            student_ids=[other.id],
        )
    assert both.value.code == "pickup_request_not_found"
    assert _reload(db_session, request.id).status == "pending"


@pytest.fixture
def owner_cleanup_transition_rows() -> Iterator[dict[str, list[UUID]]]:
    ids: dict[str, list[UUID]] = {"students": [], "staff": [], "roles": []}
    yield ids
    with connect_owner() as conn:
        conn.execute("set lock_timeout = '5s'")
        for student_id in ids["students"]:
            conn.execute("delete from public.students where id = %s", (student_id,))
        for staff_id in ids["staff"]:
            conn.execute("delete from public.staff_users where id = %s", (staff_id,))
        for role_id in ids["roles"]:
            conn.execute("delete from public.roles where id = %s", (role_id,))
        conn.commit()


@pytest.mark.cleanup_tables("pickup_requests")
def test_transition_request_concurrent(
    owner_cleanup_transition_rows: dict[str, list[UUID]],
    committing_db_session: Session,
    db_engine: Engine,
    fake_clock: FakeClock,
) -> None:
    """兩位員工同時按「完成」：只有一個 UPDATE 命中，另一個 409；completed_by 為成功者。"""
    session = committing_db_session
    student = make_student(session)
    staff_a = make_staff(session)
    staff_b = make_staff(session)
    request = make_pickup_request(session, student, service_date=_DATE)
    session.commit()
    owner_cleanup_transition_rows["students"].append(student.id)
    owner_cleanup_transition_rows["staff"] += [staff_a.id, staff_b.id]
    owner_cleanup_transition_rows["roles"] += [staff_a.role.id, staff_b.role.id]

    barrier = threading.Barrier(2)
    outcomes: dict[UUID, str] = {}
    unexpected: list[BaseException] = []

    def worker(staff_id: UUID) -> None:
        s = Session(bind=db_engine)
        try:
            barrier.wait(timeout=10)
            try:
                transition_request(
                    s,
                    request.id,
                    from_statuses=OPEN_STATUSES,
                    to_status="completed",
                    values={
                        "completed_at": fake_clock.now(),
                        "completed_by": staff_id,
                        "completion_method": "override",
                    },
                )
                s.commit()
                outcomes[staff_id] = "ok"
            except AppError as exc:
                s.rollback()
                outcomes[staff_id] = f"{exc.status}:{exc.code}"
        except BaseException as exc:
            unexpected.append(exc)
        finally:
            s.close()

    threads = [threading.Thread(target=worker, args=(sid,)) for sid in (staff_a.id, staff_b.id)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    assert unexpected == []
    assert sorted(outcomes.values()) == ["409:invalid_pickup_status", "ok"]
    winner = next(sid for sid, outcome in outcomes.items() if outcome == "ok")
    row = _reload(session, request.id)
    assert row.status == "completed"
    assert row.completed_by == winner
    assert row.completed_at == fake_clock.now()
