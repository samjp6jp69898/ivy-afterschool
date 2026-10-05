"""BACKEND-355：GET /api/parent/children/{student_id}/leaves。
BACKEND-356：POST /api/parent/leaves（家長申請請假；student_id 由 service 驗證所有權）。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.models.account import StaffUser
from app.models.leaves import StudentLeave
from app.models.parents import ParentAccount
from app.services.settings_service import clear_settings_cache
from tests.support.factories import make_guardian, make_leave, make_student
from tests.support.fake_clock import FakeClock

ParentClientFactory = Callable[..., tuple[TestClient, ParentAccount]]
StaffClientFactory = Callable[..., tuple[TestClient, StaffUser]]
AssertError = Callable[..., None]


def _url(student_id: object) -> str:
    return f"/api/parent/children/{student_id}/leaves"


def test_parent_leaves_list_success(
    parent_client: ParentClientFactory, db_session: Session
) -> None:
    client, parent = parent_client()
    ming = make_student(db_session, name="王小明")
    make_guardian(db_session, ming, parent=parent)
    make_leave(db_session, ming, start_date=date(2026, 8, 20), reason="感冒")
    make_leave(db_session, ming, start_date=date(2026, 9, 10), leave_type="personal")
    other = make_student(db_session, name="陳小華")
    make_leave(db_session, other, start_date=date(2026, 9, 10))
    db_session.commit()

    resp = client.get(_url(ming.id))

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert len(body["items"]) == 2
    first = body["items"][0]
    assert first["start_date"] == "2026-09-10"
    assert isinstance(first["can_cancel"], bool)
    assert first["can_cancel"] is True
    assert body["items"][1]["can_cancel"] is False
    assert first["leave_type_label"] == "事假"
    assert first["attachments"] == []
    assert {i["student_id"] for i in body["items"]} == {str(ming.id)}
    # 家長端不回員工姓名欄位
    assert "created_by_name" not in first
    assert "cancelled_by_name" not in first
    paged = client.get(_url(ming.id), params={"page": 2, "page_size": 1}).json()
    assert paged["total"] == 2
    assert [i["start_date"] for i in paged["items"]] == ["2026-08-20"]


def test_parent_leaves_list_422(
    parent_client: ParentClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    client, parent = parent_client()
    ming = make_student(db_session)
    make_guardian(db_session, ming, parent=parent)
    db_session.commit()

    assert_error(client.get(_url(ming.id), params={"page_size": 500}), 422, "validation_error")
    assert_error(client.get(_url(ming.id), params={"page": 0}), 422, "validation_error")
    assert_error(client.get(_url("abc")), 422, "validation_error")


def test_parent_leaves_list_401(
    api_client: TestClient, staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    assert_error(api_client.get(_url(uuid4())), 401, "unauthenticated")
    staff, _ = staff_client(permissions=["leaves:read"])
    assert_error(staff.get(_url(uuid4())), 401, "unauthenticated")


def test_parent_leaves_list_idor(
    parent_client: ParentClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    client_a, _ = parent_client()
    _, parent_b = parent_client()
    hua = make_student(db_session, name="陳小華")
    make_guardian(db_session, hua, parent=parent_b)
    make_leave(db_session, hua, start_date=date(2026, 9, 10), reason="B 的請假")
    db_session.commit()

    theirs = client_a.get(_url(hua.id))
    missing = client_a.get(_url(uuid4()))

    assert_error(theirs, 404, "student_not_found")
    assert theirs.json() == missing.json()
    assert "B 的請假" not in theirs.text


def test_parent_leaves_list_archived_child(
    parent_client: ParentClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    client, parent = parent_client()
    ming = make_student(db_session)
    make_guardian(db_session, ming, parent=parent)
    make_leave(db_session, ming, start_date=date(2026, 9, 10))
    db_session.commit()
    assert client.get(_url(ming.id)).status_code == 200

    ming.archived_at = datetime(2026, 9, 1, tzinfo=UTC)
    db_session.commit()

    assert_error(client.get(_url(ming.id)), 404, "student_not_found")


# --- BACKEND-356：POST /api/parent/leaves ---------------------------------------------------------

_CREATE_URL = "/api/parent/leaves"
_TODAY = date(2026, 9, 1)  # fake_clock 預設台北日期


def _leave_body(student_id: object, **overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "student_id": str(student_id),
        "leave_type": "sick",
        "start_date": _TODAY.isoformat(),
        "end_date": _TODAY.isoformat(),
    }
    body.update(overrides)
    return body


def _leave_count(db: Session, student_id: object) -> int:
    return db.execute(
        select(func.count()).select_from(StudentLeave).where(StudentLeave.student_id == student_id)
    ).scalar_one()


def _set_leave_window(db: Session, *, past_days: int, future_days: int) -> None:
    db.execute(
        text(
            "update public.system_settings "
            "set value = value || jsonb_build_object('past_days', :past, 'future_days', :future) "
            "where key = 'leave.window'"
        ),
        {"past": past_days, "future": future_days},
    )
    db.commit()
    clear_settings_cache()


def test_parent_leaves_create_success(
    parent_client: ParentClientFactory, db_session: Session, fake_clock: FakeClock
) -> None:
    client, parent = parent_client()
    ming = make_student(db_session, name="王小明")
    make_guardian(db_session, ming, parent=parent)
    db_session.commit()

    resp = client.post(_CREATE_URL, json=_leave_body(ming.id, reason="感冒"))

    assert resp.status_code == 201
    body = resp.json()
    assert body["student_id"] == str(ming.id)
    assert (body["leave_type"], body["leave_type_label"]) == ("sick", "病假")
    assert (body["start_date"], body["end_date"]) == ("2026-09-01", "2026-09-01")
    assert body["status"] == "active"
    assert body["can_cancel"] is True
    assert body["created_by_type"] == "parent"
    assert body["reason"] == "感冒"
    assert body["attachments"] == []
    assert body["cancelled_at"] is None
    assert "created_by_name" not in body
    stored = db_session.execute(
        select(StudentLeave).where(StudentLeave.id == body["id"])
    ).scalar_one()
    assert (stored.student_id, stored.status, stored.created_by_type) == (
        ming.id,
        "active",
        "parent",
    )
    assert stored.created_by_id == parent.id
    listed = client.get(f"/api/parent/children/{ming.id}/leaves").json()
    assert [i["id"] for i in listed["items"]] == [body["id"]]


def test_parent_leaves_create_422(
    parent_client: ParentClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    client, parent = parent_client()
    ming = make_student(db_session)
    make_guardian(db_session, ming, parent=parent)
    db_session.commit()
    _set_leave_window(db_session, past_days=30, future_days=7)

    bad_type = client.post(_CREATE_URL, json=_leave_body(ming.id, leave_type="annual"))
    reversed_dates = client.post(
        _CREATE_URL, json=_leave_body(ming.id, start_date="2026-09-03", end_date="2026-09-02")
    )
    extra = client.post(_CREATE_URL, json={**_leave_body(ming.id), "foo": 1})
    far = (_TODAY + timedelta(days=8)).isoformat()
    out_of_window = client.post(
        _CREATE_URL, json=_leave_body(ming.id, start_date=far, end_date=far)
    )

    assert_error(bad_type, 422, "validation_error")
    assert_error(reversed_dates, 422, "validation_error")
    assert_error(extra, 422, "validation_error")
    assert_error(out_of_window, 422, "leave_date_out_of_window")
    assert out_of_window.json()["error"]["details"] == {"past_days": 30, "future_days": 7}
    assert _leave_count(db_session, ming.id) == 0


def test_parent_leaves_create_401(
    api_client: TestClient, staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    assert_error(api_client.post(_CREATE_URL, json=_leave_body(uuid4())), 401, "unauthenticated")
    staff, _ = staff_client(permissions=["leaves:write"])
    assert_error(staff.post(_CREATE_URL, json=_leave_body(uuid4())), 401, "unauthenticated")


def test_parent_leaves_create_idor(
    parent_client: ParentClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    client_a, _ = parent_client()
    _, parent_b = parent_client()
    hua = make_student(db_session, name="陳小華")
    make_guardian(db_session, hua, parent=parent_b)
    db_session.commit()

    theirs = client_a.post(_CREATE_URL, json=_leave_body(hua.id))
    missing = client_a.post(_CREATE_URL, json=_leave_body(uuid4()))

    assert_error(theirs, 404, "student_not_found")
    assert theirs.json() == missing.json()
    assert _leave_count(db_session, hua.id) == 0


def test_parent_leaves_create_409(
    parent_client: ParentClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    client, parent = parent_client()
    ming = make_student(db_session, name="王小明")
    make_guardian(db_session, ming, parent=parent)
    make_leave(db_session, ming, start_date=_TODAY, end_date=_TODAY + timedelta(days=1))
    gone = make_student(db_session, name="已退班")
    gone.status = "withdrawn"
    gone.withdrawn_on = date(2026, 8, 31)
    make_guardian(db_session, gone, parent=parent)
    db_session.commit()

    overlap = client.post(_CREATE_URL, json=_leave_body(ming.id))
    withdrawn = client.post(_CREATE_URL, json=_leave_body(gone.id))

    assert_error(overlap, 409, "leave_overlap")
    assert_error(withdrawn, 409, "student_not_active")
    assert _leave_count(db_session, ming.id) == 1
    assert _leave_count(db_session, gone.id) == 0
