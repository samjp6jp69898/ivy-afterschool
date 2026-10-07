"""BACKEND-385：GET /api/admin/homework/board。
BACKEND-390：PUT /api/admin/homework/progress/{student_id}。

fake_clock 預設 2026-09-01 01:00 UTC（台北 09:00）。"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import date, datetime, time
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.account import StaffUser
from app.models.homework import HomeworkDailyProgress
from app.models.notifications import Notification
from app.notifications import outbox_jobs
from app.services.settings_service import clear_settings_cache
from tests.support.factories import (
    make_attendance,
    make_class,
    make_guardian,
    make_homework_item,
    make_homework_progress,
    make_parent,
    make_student,
)
from tests.support.fake_clock import FakeClock
from tests.support.route_audit import admin_routes_without_permission

_URL = "/api/admin/homework/board"
_DAY = date(2026, 9, 1)
StaffClientFactory = Callable[..., tuple[TestClient, StaffUser]]
AssertError = Callable[..., None]


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    clear_settings_cache()
    yield
    clear_settings_cache()


def test_admin_homework_board_success(
    staff_client: StaffClientFactory, db_session: Session
) -> None:
    class_a = make_class(db_session, name="A班")
    ming = make_student(db_session, name="王小明", student_no="H-001", class_=class_a)
    hua = make_student(db_session, name="陳小華", student_no="H-002", class_=class_a)
    make_homework_item(db_session, ming, service_date=_DAY, title="國語生字", sort_order=10)
    make_homework_item(
        db_session, ming, service_date=_DAY, title="數學習作", status="done", sort_order=20
    )
    make_homework_progress(
        db_session, ming, service_date=_DAY, overall_status="in_progress", ready_eta=time(17, 30)
    )
    make_attendance(db_session, ming, service_date=_DAY, status="present")
    make_attendance(db_session, hua, service_date=_DAY)
    client, _ = staff_client(permissions=["homework:read"])

    resp = client.get(_URL, params={"date": "2026-09-01", "class_id": str(class_a.id)})

    assert resp.status_code == 200
    body = resp.json()
    assert body["date"] == "2026-09-01"
    assert set(body) == {"date", "window", "summary", "students"}
    assert [s["name"] for s in body["students"]] == ["王小明", "陳小華"]
    ming_card = body["students"][0]
    assert ming_card["attendance_status"] == "present"
    assert [i["title"] for i in ming_card["items"]] == ["國語生字", "數學習作"]
    assert ming_card["progress"]["overall_status"] == "in_progress"
    assert ming_card["progress"]["ready_eta"] == "17:30"
    assert body["students"][1]["items"] == []
    # 不給 date → 今天（fake_clock 台北 2026-09-01）
    assert client.get(_URL, params={"class_id": str(class_a.id)}).json()["date"] == "2026-09-01"


def test_admin_homework_board_422(
    staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    client, _ = staff_client(permissions=["homework:read"])

    assert_error(client.get(_URL, params={"date": "bad"}), 422, "validation_error")
    assert_error(client.get(_URL, params={"class_id": "abc"}), 422, "validation_error")
    assert_error(client.get(_URL, params={"foo": "bar"}), 422, "validation_error")


def test_admin_homework_board_401(api_client: TestClient, assert_error: AssertError) -> None:
    assert_error(api_client.get(_URL), 401, "unauthenticated")


def test_admin_homework_board_403(
    staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    client, _ = staff_client(permissions=["attendance:read"])

    resp = client.get(_URL)

    assert_error(resp, 403, "permission_denied")
    assert resp.json()["error"]["details"] == {"required": ["homework:read"]}


def test_admin_homework_board_unknown_class(
    staff_client: StaffClientFactory, db_session: Session
) -> None:
    make_student(db_session)
    client, _ = staff_client(permissions=["homework:read"])

    resp = client.get(_URL, params={"class_id": str(uuid4())})

    assert resp.status_code == 200
    assert resp.json()["students"] == []


def test_admin_homework_board_guard_registered(app: FastAPI) -> None:
    assert admin_routes_without_permission(app) == []
    assert "get" in app.openapi()["paths"][_URL]


# --- BACKEND-390：PUT /api/admin/homework/progress/{student_id} -----------------------------------

_PROGRESS = "/api/admin/homework/progress"


@pytest.fixture(autouse=True)
def _kick_off() -> Iterator[None]:
    """設定 ETA / 標完成會 enqueue 家長通知：commit 後的 outbox kick 不實際派送。"""
    outbox_jobs.set_kick_mode("off")
    yield
    outbox_jobs.set_kick_mode("thread")


def _progress_url(student_id: object) -> str:
    return f"{_PROGRESS}/{student_id}"


def _progress_rows(db_session: Session, student_id: UUID) -> dict[date, HomeworkDailyProgress]:
    db_session.expire_all()
    rows = db_session.execute(
        select(HomeworkDailyProgress).where(HomeworkDailyProgress.student_id == student_id)
    ).scalars()
    return {row.service_date: row for row in rows}


def _parent_notifications(db_session: Session, parent_id: UUID, event: str) -> int:
    return db_session.execute(
        select(func.count())
        .select_from(Notification)
        .where(
            Notification.recipient_type == "parent",
            Notification.recipient_id == parent_id,
            Notification.event == event,
        )
    ).scalar_one()


def test_admin_homework_progress_guard_registered(app: FastAPI) -> None:
    assert admin_routes_without_permission(app) == []
    assert "put" in app.openapi()["paths"][_PROGRESS + "/{student_id}"]


def test_admin_homework_progress_success(
    staff_client: StaffClientFactory, db_session: Session, fake_clock: FakeClock
) -> None:
    ming = make_student(db_session, name="王小明")
    make_homework_item(db_session, ming, service_date=_DAY, title="數學習作")
    client, staff = staff_client(permissions=["homework:write"], display_name="林老師")

    resp = client.put(_progress_url(ming.id), json={"ready_eta": "17:30", "note": "剩數學訂正"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["ready_eta"] == "17:30"
    assert (body["student_id"], body["service_date"], body["overall_status"]) == (
        str(ming.id),
        "2026-09-01",
        "not_started",
    )
    assert (body["note"], body["eta_updated_by_name"]) == ("剩數學訂正", "林老師")
    assert datetime.fromisoformat(body["eta_updated_at"]) == fake_clock.now()
    assert set(body) == {
        "student_id",
        "service_date",
        "overall_status",
        "ready_eta",
        "note",
        "eta_updated_at",
        "eta_updated_by_name",
    }

    done = client.put(_progress_url(ming.id), json={"overall": "done"})

    assert done.status_code == 200
    assert done.json()["overall_status"] == "done"
    # 只給 overall：回應由進度列組成，保留先前設定的 ETA、說明與設定者
    assert (
        done.json()["ready_eta"],
        done.json()["note"],
        done.json()["eta_updated_by_name"],
    ) == ("17:30", "剩數學訂正", "林老師")
    assert datetime.fromisoformat(done.json()["eta_updated_at"]) == fake_clock.now()
    assert set(done.json()) == set(body)
    # 已 commit：重讀 DB
    row = _progress_rows(db_session, ming.id)[_DAY]
    assert (row.overall_status, row.ready_eta, row.note, row.eta_updated_by) == (
        "done",
        time(17, 30),
        "剩數學訂正",
        staff.id,
    )


def test_admin_homework_progress_service_date(
    staff_client: StaffClientFactory, db_session: Session
) -> None:
    ming = make_student(db_session)
    client, _ = staff_client(permissions=["homework:write"])

    resp = client.put(
        _progress_url(ming.id), json={"service_date": "2026-09-02", "note": "明天帶美勞用具"}
    )

    assert resp.status_code == 200
    assert (resp.json()["service_date"], resp.json()["note"]) == ("2026-09-02", "明天帶美勞用具")
    # 沒有設定 ETA：設定者與時間維持 null
    assert (resp.json()["ready_eta"], resp.json()["eta_updated_by_name"]) == (None, None)
    rows = _progress_rows(db_session, ming.id)
    assert set(rows) == {date(2026, 9, 2)}
    assert rows[date(2026, 9, 2)].note == "明天帶美勞用具"


def test_admin_homework_progress_422(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    ming = make_student(db_session)
    client, _ = staff_client(permissions=["homework:write"])

    for body in (
        {},
        {"service_date": "2026-09-01"},
        {"ready_eta": "7:30"},
        {"ready_eta": "24:00"},
        {"overall": None},
        {"overall": "not_started"},
        {"note": "x" * 201},
        {"note": "x", "status": "done"},
    ):
        assert_error(client.put(_progress_url(ming.id), json=body), 422, "validation_error")
    assert_error(client.put(_progress_url("abc"), json={"note": "x"}), 422, "validation_error")
    # 業務 422：超出 homework.window（預設今天前 30 天 ~ 後 7 天）
    out_of_window = client.put(
        _progress_url(ming.id), json={"service_date": "2026-09-09", "overall": "done"}
    )
    assert_error(out_of_window, 422, "invalid_service_date")
    assert out_of_window.json()["error"]["details"] == {
        "min_date": "2026-08-02",
        "max_date": "2026-09-08",
    }
    assert _progress_rows(db_session, ming.id) == {}


def test_admin_homework_progress_401(api_client: TestClient, assert_error: AssertError) -> None:
    resp = api_client.put(_progress_url(uuid4()), json={"overall": "done"})

    assert_error(resp, 401, "unauthenticated")


def test_admin_homework_progress_403(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    ming = make_student(db_session)
    client, _ = staff_client(permissions=["homework:read"])

    resp = client.put(_progress_url(ming.id), json={"overall": "done"})

    assert_error(resp, 403, "permission_denied")
    assert resp.json()["error"]["details"] == {"required": ["homework:write"]}
    assert _progress_rows(db_session, ming.id) == {}


def test_admin_homework_progress_404(
    staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    client, _ = staff_client(permissions=["homework:write"])

    assert_error(
        client.put(_progress_url(uuid4()), json={"overall": "done"}), 404, "student_not_found"
    )
    assert_error(
        client.put(_progress_url(uuid4()), json={"ready_eta": "17:30"}), 404, "student_not_found"
    )


def test_admin_homework_progress_overall_then_eta(
    staff_client: StaffClientFactory, db_session: Session
) -> None:
    ming = make_student(db_session, name="王小明")
    parent = make_parent(db_session)
    make_guardian(db_session, ming, parent=parent)
    make_homework_item(db_session, ming, service_date=_DAY)
    client, _ = staff_client(permissions=["homework:write"])

    resp = client.put(_progress_url(ming.id), json={"overall": "done", "ready_eta": "17:30"})

    assert resp.status_code == 200
    assert (resp.json()["overall_status"], resp.json()["ready_eta"]) == ("done", "17:30")
    row = _progress_rows(db_session, ming.id)[_DAY]
    assert (row.overall_status, row.ready_eta) == ("done", time(17, 30))
    # 先處理 overall：轉 done 通知一次；之後設定 ETA 時作業已完成，不發 eta_updated
    assert _parent_notifications(db_session, parent.id, "homework.done") == 1
    assert _parent_notifications(db_session, parent.id, "homework.eta_updated") == 0


def test_admin_homework_progress_partial_update(
    staff_client: StaffClientFactory, db_session: Session
) -> None:
    ming = make_student(db_session)
    make_homework_progress(
        db_session, ming, service_date=_DAY, ready_eta=time(17, 30), note="剩數學訂正"
    )
    client, _ = staff_client(permissions=["homework:write"])

    # 只給 note：ready_eta 未給 → 不動
    resp = client.put(_progress_url(ming.id), json={"note": "剩國語"})

    assert resp.status_code == 200
    assert (resp.json()["ready_eta"], resp.json()["note"]) == ("17:30", "剩國語")

    # ready_eta 給 null → 清除；note 未給 → 不動
    cleared = client.put(_progress_url(ming.id), json={"ready_eta": None})

    assert cleared.status_code == 200
    assert (cleared.json()["ready_eta"], cleared.json()["note"]) == (None, "剩國語")
    row = _progress_rows(db_session, ming.id)[_DAY]
    assert (row.ready_eta, row.note) == (None, "剩國語")


def test_admin_homework_progress_overall_auto(
    staff_client: StaffClientFactory, db_session: Session
) -> None:
    ming = make_student(db_session)
    make_homework_item(db_session, ming, service_date=_DAY, status="done")
    make_homework_item(db_session, ming, service_date=_DAY, status="doing")
    make_homework_progress(db_session, ming, service_date=_DAY, overall_status="done")
    client, _ = staff_client(permissions=["homework:write"])

    # auto：改回由項目推導（一項 done、一項 doing → in_progress）
    resp = client.put(_progress_url(ming.id), json={"overall": "auto"})

    assert resp.status_code == 200
    assert resp.json()["overall_status"] == "in_progress"
    assert _progress_rows(db_session, ming.id)[_DAY].overall_status == "in_progress"
