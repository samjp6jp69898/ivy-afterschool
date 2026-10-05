"""BACKEND-315 / 316 / 319 / 320 / 321 / 322：後台出勤 endpoint（app/api/admin/attendance.py）。

fake_clock 預設 2026-09-01 01:00 UTC（台北 09:00，週二，營業日）。check-in 會 enqueue 通知：kick
設為 off，避免背景執行緒連 DB。
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import date
from io import BytesIO
from urllib.parse import unquote
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.account import StaffUser
from app.models.attendance import StudentAttendance
from app.models.audit import AuditLog
from app.notifications import outbox_jobs
from app.services.settings_service import clear_settings_cache
from tests.support.factories import (
    make_attendance,
    make_class,
    make_leave,
    make_staff,
    make_student,
)
from tests.support.route_audit import admin_routes_without_permission

_URL = "/api/admin/attendance"
_DAY = date(2026, 9, 1)
StaffClientFactory = Callable[..., tuple[TestClient, StaffUser]]
AssertError = Callable[..., None]


@pytest.fixture(autouse=True)
def _settings_and_kick() -> Iterator[None]:
    clear_settings_cache()
    outbox_jobs.set_kick_mode("off")
    yield
    outbox_jobs.set_kick_mode("thread")
    clear_settings_cache()


def test_admin_attendance_guard_registered(app: FastAPI) -> None:
    assert admin_routes_without_permission(app) == []
    paths = app.openapi()["paths"]
    assert "get" in paths[f"{_URL}/daily"]
    assert "get" in paths[f"{_URL}/monthly"]
    assert "get" in paths[f"{_URL}/monthly/export"]
    assert "post" in paths[_URL + "/{student_id}/check-in"]
    assert "post" in paths[_URL + "/{student_id}/mark-absent"]
    assert "patch" in paths[_URL + "/{attendance_id}"]


# --- BACKEND-315：GET /daily --------------------------------------------------------------------


def test_admin_attendance_daily_success(
    staff_client: StaffClientFactory, db_session: Session
) -> None:
    class_a = make_class(db_session, name="A班")
    ming = make_student(db_session, name="王小明", student_no="A001", class_=class_a)
    make_student(db_session, name="陳小華", student_no="A002", class_=class_a)
    make_attendance(db_session, ming, service_date=_DAY, status="present")
    client, _ = staff_client(permissions=["attendance:read"])

    resp = client.get(f"{_URL}/daily", params={"date": "2026-09-01", "class_id": str(class_a.id)})

    assert resp.status_code == 200
    body = resp.json()
    assert body["date"] == "2026-09-01"
    assert body["is_service_day"] is True
    assert body["summary"]["total"] == 2
    assert body["summary"] == {
        "total": 2,
        "expected": 1,
        "present": 1,
        "left": 0,
        "absent": 0,
        "leave": 1 - 1,
    }
    assert [(i["student_name"], i["status"]) for i in body["items"]] == [
        ("王小明", "present"),
        ("陳小華", "expected"),
    ]
    assert body["items"][0]["student_name"] == "王小明"
    assert body["items"][1]["id"] is None
    # status 篩選
    present = client.get(
        f"{_URL}/daily",
        params={"date": "2026-09-01", "class_id": str(class_a.id), "status": "present"},
    ).json()
    assert [i["student_name"] for i in present["items"]] == ["王小明"]
    assert present["summary"]["total"] == 2


def test_admin_attendance_daily_422(
    staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    client, _ = staff_client(permissions=["attendance:read"])

    assert_error(
        client.get(f"{_URL}/daily", params={"date": "2026-13-01"}), 422, "validation_error"
    )
    assert_error(client.get(f"{_URL}/daily", params={"status": "gone"}), 422, "validation_error")
    assert_error(client.get(f"{_URL}/daily", params={"foo": "bar"}), 422, "validation_error")


def test_admin_attendance_daily_401(api_client: TestClient, assert_error: AssertError) -> None:
    assert_error(api_client.get(f"{_URL}/daily"), 401, "unauthenticated")


def test_admin_attendance_daily_403(
    staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    client, _ = staff_client(permissions=["students:read"])

    resp = client.get(f"{_URL}/daily")

    assert_error(resp, 403, "permission_denied")
    assert resp.json()["error"]["details"] == {"required": ["attendance:read"]}


def test_admin_attendance_daily_unknown_class(
    staff_client: StaffClientFactory, db_session: Session
) -> None:
    make_student(db_session)
    client, _ = staff_client(permissions=["attendance:read"])

    resp = client.get(f"{_URL}/daily", params={"class_id": str(uuid4())})

    assert resp.status_code == 200
    assert resp.json()["items"] == []
    assert resp.json()["summary"]["total"] == 0


# --- BACKEND-316：POST /{student_id}/check-in -----------------------------------------------------


def _check_in_url(student_id: object) -> str:
    return f"{_URL}/{student_id}/check-in"


def test_admin_check_in_success(staff_client: StaffClientFactory, db_session: Session) -> None:
    ming = make_student(db_session, name="王小明")
    client, staff = staff_client(permissions=["attendance:operate"])

    resp = client.post(_check_in_url(ming.id), json={"note": "媽媽送來"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "present"
    assert body["student_name"] == "王小明"
    assert body["check_in_source"] == "manual"
    assert body["check_in_at"] is not None
    assert body["note"] == "媽媽送來"
    # 已 commit：另開查詢看到 present
    db_session.expire_all()
    row = db_session.execute(
        select(StudentAttendance).where(
            StudentAttendance.student_id == ming.id, StudentAttendance.service_date == _DAY
        )
    ).scalar_one()
    assert (row.status, row.updated_by) == ("present", staff.id)
    # body 可省略
    hua = make_student(db_session, name="陳小華")
    db_session.commit()
    assert client.post(_check_in_url(hua.id)).status_code == 200


def test_admin_check_in_422(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    ming = make_student(db_session)
    client, _ = staff_client(permissions=["attendance:operate"])

    assert_error(client.post(_check_in_url("abc")), 422, "validation_error")
    assert_error(
        client.post(_check_in_url(ming.id), json={"note": "x", "time": "15:00"}),
        422,
        "validation_error",
    )


def test_admin_check_in_401(api_client: TestClient, assert_error: AssertError) -> None:
    assert_error(api_client.post(_check_in_url(uuid4())), 401, "unauthenticated")


def test_admin_check_in_403(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    ming = make_student(db_session)
    client, _ = staff_client(permissions=["attendance:read"])

    resp = client.post(_check_in_url(ming.id))

    assert_error(resp, 403, "permission_denied")
    assert resp.json()["error"]["details"] == {"required": ["attendance:operate"]}


def test_admin_check_in_409(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    present = make_student(db_session, name="已到班")
    make_attendance(db_session, present, service_date=_DAY, status="present")
    on_leave = make_student(db_session, name="請假中")
    leave = make_leave(db_session, on_leave, start_date=_DAY)
    make_attendance(db_session, on_leave, service_date=_DAY, status="leave", leave=leave)
    client, _ = staff_client(permissions=["attendance:operate"])

    assert_error(client.post(_check_in_url(present.id)), 409, "already_checked_in")
    assert_error(client.post(_check_in_url(on_leave.id)), 409, "student_on_leave")
    assert_error(client.post(_check_in_url(uuid4())), 404, "student_not_found")


# --- BACKEND-319：POST /{student_id}/mark-absent --------------------------------------------------


def _mark_absent_url(student_id: object) -> str:
    return f"{_URL}/{student_id}/mark-absent"


def test_admin_mark_absent_success(staff_client: StaffClientFactory, db_session: Session) -> None:
    ming = make_student(db_session, name="王小明")
    make_attendance(db_session, ming, service_date=_DAY)
    client, _ = staff_client(permissions=["attendance:operate"])

    resp = client.post(_mark_absent_url(ming.id), json={"note": "家長來電"})

    assert resp.status_code == 200
    assert resp.json()["status"] == "absent"
    assert resp.json()["note"] == "家長來電"
    db_session.expire_all()
    row = db_session.execute(
        select(StudentAttendance).where(StudentAttendance.student_id == ming.id)
    ).scalar_one()
    assert row.status == "absent"


def test_admin_mark_absent_422(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    ming = make_student(db_session)
    client, _ = staff_client(permissions=["attendance:operate"])

    assert_error(client.post(_mark_absent_url("abc")), 422, "validation_error")
    assert_error(
        client.post(_mark_absent_url(ming.id), json={"reason": "x"}), 422, "validation_error"
    )


def test_admin_mark_absent_401(api_client: TestClient, assert_error: AssertError) -> None:
    assert_error(api_client.post(_mark_absent_url(uuid4())), 401, "unauthenticated")


def test_admin_mark_absent_403(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    ming = make_student(db_session)
    client, _ = staff_client(permissions=["attendance:read"])

    assert_error(client.post(_mark_absent_url(ming.id)), 403, "permission_denied")


def test_admin_mark_absent_409(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    present = make_student(db_session)
    make_attendance(db_session, present, service_date=_DAY, status="present")
    client, _ = staff_client(permissions=["attendance:operate"])

    assert_error(client.post(_mark_absent_url(present.id)), 409, "already_checked_in")


# --- BACKEND-320：PATCH /{attendance_id} ----------------------------------------------------------


def _amend_url(attendance_id: object) -> str:
    return f"{_URL}/{attendance_id}"


def test_admin_attendance_amend_success(
    api_client: TestClient,
    app: FastAPI,
    db_session: Session,
    login_staff: Callable[[TestClient, StaffUser], None],
) -> None:
    ming = make_student(db_session, name="王小明")
    row = make_attendance(db_session, ming, service_date=_DAY, status="present")
    staff = make_staff(db_session, permissions=["attendance:amend"])
    db_session.commit()
    # 預設 TestClient 的來源位址是 'testclient'（非 IP，RequestMeta 存 None）：指定真實 IP 才能驗
    # audit.ip
    client = TestClient(app, base_url="http://testserver", client=("203.0.113.5", 50000))
    login_staff(client, staff)

    resp = client.patch(_amend_url(row.id), json={"status": "expected", "reason": "誤刷"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "expected"
    assert body["check_in_at"] is None
    assert body["id"] == str(row.id)
    log = db_session.execute(
        select(AuditLog).where(
            AuditLog.action == "attendance.amend", AuditLog.entity_id == str(row.id)
        )
    ).scalar_one()
    assert log.actor_id == staff.id
    assert log.ip == "203.0.113.5"
    assert log.after is not None
    assert log.after["reason"] == "誤刷"


def test_admin_attendance_amend_422(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    row = make_attendance(db_session, make_student(db_session), service_date=_DAY, status="present")
    client, _ = staff_client(permissions=["attendance:amend"])

    # 只給 reason：schema 要求除 reason 外至少一欄
    assert_error(client.patch(_amend_url(row.id), json={"reason": "x"}), 422, "validation_error")
    # 缺 reason
    assert_error(
        client.patch(_amend_url(row.id), json={"status": "present"}), 422, "validation_error"
    )
    assert_error(
        client.patch(_amend_url(row.id), json={"status": "leave", "reason": "x"}),
        422,
        "validation_error",
    )
    assert_error(client.patch(_amend_url("abc"), json={"reason": "x"}), 422, "validation_error")


def test_admin_attendance_amend_401(api_client: TestClient, assert_error: AssertError) -> None:
    resp = api_client.patch(_amend_url(uuid4()), json={"status": "expected", "reason": "x"})

    assert_error(resp, 401, "unauthenticated")


def test_admin_attendance_amend_403(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    row = make_attendance(db_session, make_student(db_session), service_date=_DAY, status="present")
    client, _ = staff_client(permissions=["attendance:operate"])

    resp = client.patch(_amend_url(row.id), json={"status": "expected", "reason": "x"})

    assert_error(resp, 403, "permission_denied")
    assert resp.json()["error"]["details"] == {"required": ["attendance:amend"]}
    db_session.expire_all()
    assert row.status == "present"


def test_admin_attendance_amend_business(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    student = make_student(db_session)
    leave = make_leave(db_session, student, start_date=_DAY)
    row = make_attendance(db_session, student, service_date=_DAY, status="leave", leave=leave)
    client, _ = staff_client(permissions=["attendance:amend"])

    managed = client.patch(_amend_url(row.id), json={"status": "absent", "reason": "x"})
    missing = client.patch(_amend_url(uuid4()), json={"status": "expected", "reason": "x"})

    assert_error(managed, 409, "attendance_managed_by_leave")
    assert_error(missing, 404, "attendance_not_found")


# --- BACKEND-321：GET /monthly --------------------------------------------------------------------


def test_admin_attendance_monthly_success(
    staff_client: StaffClientFactory, db_session: Session
) -> None:
    class_a = make_class(db_session, name="A班")
    ming = make_student(db_session, name="王小明", class_=class_a)
    make_attendance(db_session, ming, service_date=_DAY, status="present")
    client, _ = staff_client(permissions=["attendance:read"])

    resp = client.get(f"{_URL}/monthly", params={"month": "2026-09", "class_id": str(class_a.id)})

    assert resp.status_code == 200
    body = resp.json()
    assert body["month"] == "2026-09"
    assert body["class_name"] == "A班"
    assert len(body["days"]) == 30
    assert body["days"][0] == {"date": "2026-09-01", "weekday": 1, "is_service_day": True}
    assert [s["name"] for s in body["students"]] == ["王小明"]
    assert body["students"][0]["statuses"][0] == "present"
    assert len(body["students"][0]["statuses"]) == 30
    assert set(body) == {"month", "class_id", "class_name", "days", "students", "totals"}


def test_admin_attendance_monthly_422(
    staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    client, _ = staff_client(permissions=["attendance:read"])

    assert_error(client.get(f"{_URL}/monthly"), 422, "validation_error")
    assert_error(client.get(f"{_URL}/monthly", params={"month": "2026-9"}), 422, "validation_error")
    assert_error(
        client.get(f"{_URL}/monthly", params={"month": "2026-09", "class_id": "abc"}),
        422,
        "validation_error",
    )


def test_admin_attendance_monthly_401(api_client: TestClient, assert_error: AssertError) -> None:
    assert_error(
        api_client.get(f"{_URL}/monthly", params={"month": "2026-09"}), 401, "unauthenticated"
    )


def test_admin_attendance_monthly_403(
    staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    client, _ = staff_client(permissions=["students:read"])

    assert_error(
        client.get(f"{_URL}/monthly", params={"month": "2026-09"}), 403, "permission_denied"
    )


def test_admin_attendance_monthly_class_filter(
    staff_client: StaffClientFactory, db_session: Session
) -> None:
    class_a = make_class(db_session, name="A班")
    class_b = make_class(db_session, name="B班")
    make_student(db_session, name="王小明", class_=class_a)
    make_student(db_session, name="林小安", class_=class_a)
    make_student(db_session, name="陳小華", class_=class_b)
    client, _ = staff_client(permissions=["attendance:read"])

    body = client.get(
        f"{_URL}/monthly", params={"month": "2026-09", "class_id": str(class_a.id)}
    ).json()

    assert body["class_id"] == str(class_a.id)
    assert {s["name"] for s in body["students"]} == {"王小明", "林小安"}
    assert all(s["class_name"] == "A班" for s in body["students"])


# --- BACKEND-322：GET /monthly/export -------------------------------------------------------------

_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def test_admin_attendance_export_success(
    staff_client: StaffClientFactory, db_session: Session
) -> None:
    class_a = make_class(db_session, name="A班")
    ming = make_student(db_session, name="王小明", student_no="A001", class_=class_a)
    make_attendance(db_session, ming, service_date=_DAY, status="present")
    client, _ = staff_client(permissions=["attendance:read"])

    resp = client.get(
        f"{_URL}/monthly/export", params={"month": "2026-09", "class_id": str(class_a.id)}
    )

    assert resp.status_code == 200
    assert resp.headers["content-type"] == _XLSX
    disposition = resp.headers["content-disposition"]
    assert disposition.startswith("attachment; filename*=UTF-8''")
    assert unquote(disposition.split("''", 1)[1]) == "出勤月報_A班_2026-09.xlsx"
    assert resp.headers["cache-control"] == "no-store"
    ws = load_workbook(BytesIO(resp.content))["出勤月報"]
    assert "2026年9月出勤月報" in str(ws["A1"].value)
    assert str(ws["A1"].value).startswith("A班")
    assert ws["A3"].value == "A001"
    # 不給 class_id：標題為「全部」
    everyone = client.get(f"{_URL}/monthly/export", params={"month": "2026-09"})
    assert everyone.status_code == 200
    assert unquote(everyone.headers["content-disposition"].split("''", 1)[1]) == (
        "出勤月報_全部_2026-09.xlsx"
    )


def test_admin_attendance_export_422(
    staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    client, _ = staff_client(permissions=["attendance:read"])

    assert_error(
        client.get(f"{_URL}/monthly/export", params={"month": "bad"}), 422, "validation_error"
    )
    assert_error(client.get(f"{_URL}/monthly/export"), 422, "validation_error")


def test_admin_attendance_export_401(api_client: TestClient, assert_error: AssertError) -> None:
    assert_error(
        api_client.get(f"{_URL}/monthly/export", params={"month": "2026-09"}),
        401,
        "unauthenticated",
    )


def test_admin_attendance_export_403(
    staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    client, _ = staff_client(permissions=["students:read"])

    assert_error(
        client.get(f"{_URL}/monthly/export", params={"month": "2026-09"}),
        403,
        "permission_denied",
    )


def test_admin_attendance_export_404(
    staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    client, _ = staff_client(permissions=["attendance:read"])

    resp = client.get(
        f"{_URL}/monthly/export", params={"month": "2026-09", "class_id": str(uuid4())}
    )

    assert_error(resp, 404, "class_not_found")


# --- BACKEND-317：POST /{student_id}/check-out ----------------------------------------------------


def _check_out_url(student_id: object) -> str:
    return f"{_URL}/{student_id}/check-out"


def test_admin_check_out_success(staff_client: StaffClientFactory, db_session: Session) -> None:
    ming = make_student(db_session, name="王小明")
    make_attendance(db_session, ming, service_date=_DAY, status="present")
    client, staff = staff_client(permissions=["attendance:operate"])

    resp = client.post(_check_out_url(ming.id), json={"note": "爸爸來接"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "left"
    assert body["check_out_source"] == "manual"
    assert body["check_out_at"] is not None
    assert body["note"] == "爸爸來接"
    db_session.expire_all()
    row = db_session.execute(
        select(StudentAttendance).where(StudentAttendance.student_id == ming.id)
    ).scalar_one()
    assert (row.status, row.updated_by) == ("left", staff.id)
    assert row.check_out_at is not None


def test_admin_check_out_422(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    ming = make_student(db_session)
    make_attendance(db_session, ming, service_date=_DAY, status="present")
    client, _ = staff_client(permissions=["attendance:operate"])

    assert_error(
        client.post(_check_out_url(ming.id), json={"note": "x" * 201}), 422, "validation_error"
    )
    assert_error(client.post(_check_out_url("abc")), 422, "validation_error")
    assert_error(
        client.post(_check_out_url(ming.id), json={"time": "18:00"}), 422, "validation_error"
    )


def test_admin_check_out_401(api_client: TestClient, assert_error: AssertError) -> None:
    assert_error(api_client.post(_check_out_url(uuid4())), 401, "unauthenticated")


def test_admin_check_out_403(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    ming = make_student(db_session)
    make_attendance(db_session, ming, service_date=_DAY, status="present")
    client, _ = staff_client(permissions=["attendance:read"])

    resp = client.post(_check_out_url(ming.id))

    assert_error(resp, 403, "permission_denied")
    assert resp.json()["error"]["details"] == {"required": ["attendance:operate"]}


def test_admin_check_out_409(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    expected = make_student(db_session, name="尚未到班")
    make_attendance(db_session, expected, service_date=_DAY)
    no_row = make_student(db_session, name="沒有出勤列")
    client, _ = staff_client(permissions=["attendance:operate"])

    assert_error(client.post(_check_out_url(expected.id)), 409, "not_checked_in")
    assert_error(client.post(_check_out_url(no_row.id)), 409, "not_checked_in")
    assert_error(client.post(_check_out_url(uuid4())), 404, "student_not_found")
