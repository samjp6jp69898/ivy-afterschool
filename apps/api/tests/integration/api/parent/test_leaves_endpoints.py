"""BACKEND-355：GET /api/parent/children/{student_id}/leaves。
BACKEND-356：POST /api/parent/leaves（家長申請請假；student_id 由 service 驗證所有權）。
BACKEND-357：POST /api/parent/leaves/{id}/cancel（未開始整筆取消、已開始取消剩餘日子）。
BACKEND-358：POST /api/parent/leaves/{id}/attachments（multipart 上傳附件）。"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import httpx2
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.storage import StorageError
from app.models.account import StaffUser
from app.models.attendance import StudentAttendance
from app.models.leaves import StudentLeave, StudentLeaveAttachment
from app.models.parents import ParentAccount
from app.services.settings_service import clear_settings_cache
from tests.support.factories import (
    make_attendance,
    make_guardian,
    make_leave,
    make_leave_attachment,
    make_student,
)
from tests.support.fake_clock import FakeClock
from tests.support.fake_storage import FakeStorage

ParentClientFactory = Callable[..., tuple[TestClient, ParentAccount]]
StaffClientFactory = Callable[..., tuple[TestClient, StaffUser]]
AssertError = Callable[..., None]


@pytest.fixture(autouse=True)
def _settings_cache() -> Iterator[None]:
    """測試內改 system_settings 後要清快取，結束也清（快取是行程層級，不隨交易 rollback）。"""
    clear_settings_cache()
    yield
    clear_settings_cache()


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


# --- BACKEND-357：POST /api/parent/leaves/{id}/cancel ---------------------------------------------

_WEDNESDAY = "2026-09-09T10:00:00+08:00"  # 台北週三；同週的週一是 9/7、週五是 9/11


def _cancel_url(leave_id: object) -> str:
    return f"/api/parent/leaves/{leave_id}/cancel"


def _reload(db: Session, leave_id: object) -> StudentLeave:
    """跳過 identity map 重讀：handler 沒 commit 時，請求結束的 rollback 會讓這裡讀到舊值。"""
    return db.execute(
        select(StudentLeave)
        .where(StudentLeave.id == leave_id)
        .execution_options(populate_existing=True)
    ).scalar_one()


def _attendance_on(db: Session, student_id: object, day: date) -> StudentAttendance:
    return db.execute(
        select(StudentAttendance)
        .where(StudentAttendance.student_id == student_id, StudentAttendance.service_date == day)
        .execution_options(populate_existing=True)
    ).scalar_one()


@pytest.mark.clock(_WEDNESDAY)
def test_parent_leaves_cancel_success(
    parent_client: ParentClientFactory, db_session: Session, fake_clock: FakeClock
) -> None:
    client, parent = parent_client()
    ming = make_student(db_session, name="王小明")
    hua = make_student(db_session, name="陳小華")
    make_guardian(db_session, ming, parent=parent)
    make_guardian(db_session, hua, parent=parent)
    # 陳小華：今天（9/9）開始的請假 → 整筆取消、出勤還原
    starts_today = make_leave(
        db_session,
        hua,
        start_date=date(2026, 9, 9),
        end_date=date(2026, 9, 10),
        leave_type="personal",
        reason="看牙醫",
    )
    make_attendance(
        db_session, hua, service_date=date(2026, 9, 9), status="leave", leave=starts_today
    )
    # 王小明：週一~五的請假在週三取消 → 只取消今天起的日子
    midweek = make_leave(
        db_session, ming, start_date=date(2026, 9, 7), end_date=date(2026, 9, 11), reason="感冒"
    )
    for day in range(7, 12):
        make_attendance(
            db_session, ming, service_date=date(2026, 9, day), status="leave", leave=midweek
        )
    db_session.commit()

    whole = client.post(_cancel_url(starts_today.id))
    truncated = client.post(_cancel_url(midweek.id))

    assert whole.status_code == 200
    whole_body = whole.json()
    assert whole_body["id"] == str(starts_today.id)
    assert whole_body["student_id"] == str(hua.id)
    assert (whole_body["status"], whole_body["can_cancel"]) == ("cancelled", False)
    assert (whole_body["start_date"], whole_body["end_date"]) == ("2026-09-09", "2026-09-10")
    assert (whole_body["leave_type_label"], whole_body["reason"]) == ("事假", "看牙醫")
    assert datetime.fromisoformat(whole_body["cancelled_at"]) == fake_clock.now()
    assert "cancelled_by_name" not in whole_body
    assert truncated.status_code == 200
    truncated_body = truncated.json()
    assert truncated_body["id"] == str(midweek.id)
    assert truncated_body["status"] == "active"
    assert (truncated_body["start_date"], truncated_body["end_date"]) == (
        "2026-09-07",
        "2026-09-08",
    )
    assert (truncated_body["can_cancel"], truncated_body["cancelled_at"]) == (False, None)

    # 請求後重讀 DB：請假與出勤的變更都已 commit
    db_session.expire_all()
    stored_whole = _reload(db_session, starts_today.id)
    assert (
        stored_whole.status,
        stored_whole.cancelled_by_type,
        stored_whole.cancelled_by_id,
    ) == ("cancelled", "parent", parent.id)
    stored_mid = _reload(db_session, midweek.id)
    assert (stored_mid.status, stored_mid.end_date, stored_mid.cancelled_at) == (
        "active",
        date(2026, 9, 8),
        None,
    )
    assert _attendance_on(db_session, hua.id, date(2026, 9, 9)).status == "expected"
    assert [
        _attendance_on(db_session, ming.id, date(2026, 9, day)).status for day in range(7, 12)
    ] == ["leave", "leave", "expected", "expected", "expected"]
    listed = client.get(f"/api/parent/children/{ming.id}/leaves").json()
    assert [(i["id"], i["end_date"], i["can_cancel"]) for i in listed["items"]] == [
        (str(midweek.id), "2026-09-08", False)
    ]


@pytest.mark.clock(_WEDNESDAY)
def test_parent_leaves_cancel_response_includes_attachments(
    parent_client: ParentClientFactory, db_session: Session
) -> None:
    client, parent = parent_client()
    ming = make_student(db_session, name="王小明")
    make_guardian(db_session, ming, parent=parent)
    leave = make_leave(db_session, ming, start_date=date(2026, 9, 10), end_date=date(2026, 9, 11))
    attachment = make_leave_attachment(db_session, leave, ext="pdf")
    db_session.commit()

    resp = client.post(_cancel_url(leave.id))

    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"
    [item] = resp.json()["attachments"]
    assert item["id"] == str(attachment.id)
    assert (item["mime_type"], item["size_bytes"]) == ("application/pdf", 1024)
    assert (
        item["url"] == f"https://storage.test/leave-attachments/{attachment.storage_path}?exp=300"
    )
    assert "storage_path" not in resp.text


@pytest.mark.clock(_WEDNESDAY)
def test_parent_leaves_cancel_sign_failure_still_cancels(
    parent_client: ParentClientFactory, db_session: Session, fake_storage: FakeStorage
) -> None:
    """附件簽名失敗不讓取消失敗：該附件 url 為 null（與列表 BACKEND-348 一致），請假仍已取消。"""
    client, parent = parent_client()
    ming = make_student(db_session, name="王小明")
    make_guardian(db_session, ming, parent=parent)
    leave = make_leave(db_session, ming, start_date=date(2026, 9, 10), end_date=date(2026, 9, 11))
    make_leave_attachment(db_session, leave, ext="jpg")
    db_session.commit()
    fake_storage.sign_error = StorageError("S3 generate_presigned_url 失敗")

    resp = client.post(_cancel_url(leave.id))

    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"
    assert [a["url"] for a in resp.json()["attachments"]] == [None]
    db_session.expire_all()
    assert _reload(db_session, leave.id).status == "cancelled"


def test_parent_leaves_cancel_422(
    parent_client: ParentClientFactory, assert_error: AssertError
) -> None:
    client, _ = parent_client()

    assert_error(client.post(_cancel_url("abc")), 422, "validation_error")


def test_parent_leaves_cancel_401(
    api_client: TestClient, staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    assert_error(api_client.post(_cancel_url(uuid4())), 401, "unauthenticated")
    staff, _ = staff_client(permissions=["leaves:write"])
    assert_error(staff.post(_cancel_url(uuid4())), 401, "unauthenticated")


@pytest.mark.clock(_WEDNESDAY)
def test_parent_leaves_cancel_idor(
    parent_client: ParentClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    client_a, _ = parent_client()
    _, parent_b = parent_client()
    hua = make_student(db_session, name="陳小華")
    make_guardian(db_session, hua, parent=parent_b)
    theirs_leave = make_leave(
        db_session, hua, start_date=date(2026, 9, 7), end_date=date(2026, 9, 11), reason="B 的請假"
    )
    make_attendance(
        db_session, hua, service_date=date(2026, 9, 9), status="leave", leave=theirs_leave
    )
    db_session.commit()

    theirs = client_a.post(_cancel_url(theirs_leave.id))
    missing = client_a.post(_cancel_url(uuid4()))

    assert_error(theirs, 404, "leave_not_found")
    assert theirs.json() == missing.json()
    assert "B 的請假" not in theirs.text
    db_session.expire_all()
    stored = _reload(db_session, theirs_leave.id)
    assert (stored.status, stored.end_date) == ("active", date(2026, 9, 11))
    assert _attendance_on(db_session, hua.id, date(2026, 9, 9)).status == "leave"


@pytest.mark.clock("2026-09-10T10:00:00+08:00")  # 週四
def test_parent_leaves_cancel_409(
    parent_client: ParentClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    client, parent = parent_client()
    ming = make_student(db_session, name="王小明")
    hua = make_student(db_session, name="陳小華")
    make_guardian(db_session, ming, parent=parent)
    make_guardian(db_session, hua, parent=parent)
    ended = make_leave(db_session, ming, start_date=date(2026, 9, 7), end_date=date(2026, 9, 8))
    cancelled = make_leave(db_session, ming, start_date=date(2026, 9, 14), status="cancelled")
    midweek = make_leave(db_session, hua, start_date=date(2026, 9, 7), end_date=date(2026, 9, 11))
    db_session.commit()

    already_ended = client.post(_cancel_url(ended.id))
    not_active = client.post(_cancel_url(cancelled.id))
    first = client.post(_cancel_url(midweek.id))
    double_tap = client.post(_cancel_url(midweek.id))

    assert_error(already_ended, 409, "leave_already_ended")
    assert_error(not_active, 409, "leave_not_active")
    # 已開始的請假取消一次後 end_date 變成昨天；連點第二次沒有可取消的日子
    assert first.status_code == 200
    assert first.json()["end_date"] == "2026-09-09"
    assert_error(double_tap, 409, "leave_already_ended")
    db_session.expire_all()
    stored_ended = _reload(db_session, ended.id)
    assert (stored_ended.status, stored_ended.end_date) == ("active", date(2026, 9, 8))
    assert _reload(db_session, cancelled.id).status == "cancelled"
    assert _reload(db_session, midweek.id).end_date == date(2026, 9, 9)


# --- BACKEND-358：POST /api/parent/leaves/{id}/attachments ----------------------------------------

_JPEG = b"\xff\xd8\xff\xe0" + b"0" * 100
_PDF = b"%PDF-1.7\n" + b"0" * 50
_EXE = b"MZ\x90\x00" + b"0" * 100  # Windows 執行檔檔頭


def _attachments_url(leave_id: object) -> str:
    return f"/api/parent/leaves/{leave_id}/attachments"


def _post_attachment(
    client: TestClient,
    leave_id: object,
    content: bytes = _JPEG,
    *,
    filename: str = "diagnosis-note.jpg",
    content_type: str = "image/jpeg",
) -> httpx2.Response:
    return client.post(
        _attachments_url(leave_id), files={"file": (filename, content, content_type)}
    )


def _attachment_count(db: Session, leave_id: object) -> int:
    return db.execute(
        select(func.count())
        .select_from(StudentLeaveAttachment)
        .where(StudentLeaveAttachment.leave_id == leave_id)
    ).scalar_one()


def _own_leave(db: Session, parent: ParentAccount, *, name: str = "王小明") -> StudentLeave:
    student = make_student(db, name=name)
    make_guardian(db, student, parent=parent)
    return make_leave(db, student, start_date=_TODAY, end_date=_TODAY + timedelta(days=1))


def _set_leave_limit(db: Session, field: str, value: int) -> None:
    db.execute(
        text(
            "update public.system_settings "
            "set value = jsonb_set(value, cast(:path as text[]), to_jsonb(cast(:v as int))) "
            "where key = 'leave.window'"
        ),
        {"path": "{" + field + "}", "v": value},
    )
    db.commit()
    clear_settings_cache()


def test_parent_leave_attachment_success(
    parent_client: ParentClientFactory, db_session: Session, fake_storage: FakeStorage
) -> None:
    client, parent = parent_client()
    leave = _own_leave(db_session, parent)
    db_session.commit()

    resp = _post_attachment(client, leave.id)

    assert resp.status_code == 201
    body = resp.json()
    assert set(body) == {"id", "mime_type", "size_bytes", "created_at", "url"}
    assert body["mime_type"] == "image/jpeg"
    assert body["size_bytes"] == len(_JPEG)
    assert isinstance(body["url"], str)
    # 物件進 leave-attachments 分區，路徑為 <leave_id>/<hex>.jpg，不含使用者檔名
    (((bucket, path), content),) = fake_storage.objects.items()
    assert (bucket, content) == ("leave-attachments", _JPEG)
    assert path.startswith(f"{leave.id}/")
    assert path.endswith(".jpg")
    assert "diagnosis-note" not in path
    assert fake_storage.content_types[(bucket, path)] == "image/jpeg"
    assert body["url"] == f"https://storage.test/leave-attachments/{path}?exp=300"
    assert "storage_path" not in resp.text
    # 請求後重讀 DB 與列表：附件已 commit
    db_session.expire_all()
    assert _attachment_count(db_session, leave.id) == 1
    listed = client.get(f"/api/parent/children/{leave.student_id}/leaves").json()
    assert [a["id"] for a in listed["items"][0]["attachments"]] == [body["id"]]
    # PDF 同樣可上傳
    pdf = _post_attachment(
        client, leave.id, _PDF, filename="report.pdf", content_type="application/pdf"
    )
    assert pdf.status_code == 201
    assert pdf.json()["mime_type"] == "application/pdf"
    assert len(fake_storage.objects) == 2


def test_parent_leave_attachment_422(
    parent_client: ParentClientFactory,
    db_session: Session,
    assert_error: AssertError,
    fake_storage: FakeStorage,
) -> None:
    client, parent = parent_client()
    leave = _own_leave(db_session, parent)
    db_session.commit()

    wrong_field = client.post(
        _attachments_url(leave.id), files={"photo": ("a.jpg", _JPEG, "image/jpeg")}
    )
    no_body = client.post(_attachments_url(leave.id))
    empty = _post_attachment(client, leave.id, b"")
    bad_path = _post_attachment(client, "abc")

    assert_error(wrong_field, 422, "validation_error")
    assert_error(no_body, 422, "validation_error")
    assert_error(empty, 422, "file_empty")
    assert_error(bad_path, 422, "validation_error")
    assert fake_storage.objects == {}
    db_session.expire_all()
    assert _attachment_count(db_session, leave.id) == 0


def test_parent_leave_attachment_401(
    api_client: TestClient,
    staff_client: StaffClientFactory,
    assert_error: AssertError,
    fake_storage: FakeStorage,
) -> None:
    assert_error(_post_attachment(api_client, uuid4()), 401, "unauthenticated")
    staff, _ = staff_client(permissions=["leaves:write"])
    assert_error(_post_attachment(staff, uuid4()), 401, "unauthenticated")
    assert fake_storage.objects == {}


def test_parent_leave_attachment_idor(
    parent_client: ParentClientFactory,
    db_session: Session,
    assert_error: AssertError,
    fake_storage: FakeStorage,
) -> None:
    client_a, _ = parent_client()
    _, parent_b = parent_client()
    theirs_leave = _own_leave(db_session, parent_b, name="陳小華")
    db_session.commit()

    theirs = _post_attachment(client_a, theirs_leave.id)
    missing = _post_attachment(client_a, uuid4())

    assert_error(theirs, 404, "leave_not_found")
    assert theirs.json() == missing.json()
    assert fake_storage.objects == {}
    db_session.expire_all()
    assert _attachment_count(db_session, theirs_leave.id) == 0


def test_parent_leave_attachment_business(
    parent_client: ParentClientFactory,
    db_session: Session,
    assert_error: AssertError,
    fake_storage: FakeStorage,
) -> None:
    client, parent = parent_client()
    leave = _own_leave(db_session, parent)
    db_session.commit()
    _set_leave_limit(db_session, "max_attachments", 3)

    exe = _post_attachment(
        client, leave.id, _EXE, filename="virus.exe", content_type="application/octet-stream"
    )
    disguised = _post_attachment(client, leave.id, _EXE)  # 檔名與 content-type 偽裝成 JPEG
    assert_error(exe, 415, "unsupported_file_type")
    assert_error(disguised, 415, "unsupported_file_type")
    assert fake_storage.objects == {}

    for _ in range(3):
        assert _post_attachment(client, leave.id).status_code == 201
    fourth = _post_attachment(client, leave.id)

    assert_error(fourth, 409, "attachment_limit_reached")
    assert fourth.json()["error"]["details"] == {"max_attachments": 3}
    assert len(fake_storage.objects) == 3
    db_session.expire_all()
    assert _attachment_count(db_session, leave.id) == 3


def test_parent_leave_attachment_too_large(
    parent_client: ParentClientFactory,
    db_session: Session,
    assert_error: AssertError,
    fake_storage: FakeStorage,
) -> None:
    client, parent = parent_client()
    leave = _own_leave(db_session, parent)
    db_session.commit()
    _set_leave_limit(db_session, "max_attachment_mb", 1)

    at_limit = _post_attachment(client, leave.id, _JPEG + b"0" * (1024 * 1024 - len(_JPEG)))
    over = _post_attachment(client, leave.id, _JPEG + b"0" * (1024 * 1024 - len(_JPEG) + 1))

    assert at_limit.status_code == 201
    assert_error(over, 413, "file_too_large")
    assert over.json()["error"]["details"] == {"max_bytes": 1024 * 1024}
    assert len(fake_storage.objects) == 1
    db_session.expire_all()
    assert _attachment_count(db_session, leave.id) == 1


def test_parent_leave_attachment_cancelled_leave(
    parent_client: ParentClientFactory,
    db_session: Session,
    assert_error: AssertError,
    fake_storage: FakeStorage,
) -> None:
    client, parent = parent_client()
    ming = make_student(db_session, name="王小明")
    make_guardian(db_session, ming, parent=parent)
    cancelled = make_leave(db_session, ming, start_date=_TODAY, status="cancelled")
    db_session.commit()

    resp = _post_attachment(client, cancelled.id)

    assert_error(resp, 409, "leave_not_active")
    assert fake_storage.objects == {}
    db_session.expire_all()
    assert _attachment_count(db_session, cancelled.id) == 0


def test_parent_leave_attachment_storage_unavailable(
    parent_client: ParentClientFactory,
    db_session: Session,
    assert_error: AssertError,
    fake_storage: FakeStorage,
) -> None:
    client, parent = parent_client()
    leave = _own_leave(db_session, parent)
    db_session.commit()
    fake_storage.upload_error = StorageError("S3 put_object 失敗：InternalError（HTTP 500）")

    resp = _post_attachment(client, leave.id)

    assert_error(resp, 502, "storage_unavailable")
    assert "InternalError" not in resp.text
    assert fake_storage.objects == {}
    db_session.expire_all()
    assert _attachment_count(db_session, leave.id) == 0
