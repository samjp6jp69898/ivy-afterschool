"""BACKEND-441：GET /api/parent/pickup/requests/today。
BACKEND-444 / 445 / 446：家長端常用接送人（列表、新增 multipart 含照片、刪除）。
BACKEND-447 / 448：家長端代理接送授權（列表、建立；接送碼只回一次、Cache-Control: no-store）。"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import date, timedelta
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.account import StaffUser
from app.models.parents import ParentAccount
from app.models.pickup import PickupPerson
from app.models.students import Student
from app.services.settings_service import clear_settings_cache
from tests.support.factories import (
    make_guardian,
    make_pickup_authorization,
    make_pickup_person,
    make_pickup_request,
    make_student,
)
from tests.support.fake_clock import FakeClock
from tests.support.fake_storage import FakeStorage

ParentClientFactory = Callable[..., tuple[TestClient, ParentAccount]]
StaffClientFactory = Callable[..., tuple[TestClient, StaffUser]]
AssertError = Callable[..., None]
_TODAY = date(2026, 9, 1)  # fake_clock 預設台北日期
_JPEG = b"\xff\xd8\xff\xe0" + b"0" * 100
_PDF = b"%PDF-1.7\n" + b"0" * 50
_PERSON_FORM = {"name": "李阿姨", "relation": "阿姨", "phone": "0912-000-101"}


@pytest.fixture(autouse=True)
def _settings_cache() -> Iterator[None]:
    """測試內改 system_settings 後要清快取，結束也清（快取是行程層級，不隨交易 rollback）。"""
    clear_settings_cache()
    yield
    clear_settings_cache()


def _set_setting(db: Session, key: str, field: str, value: int) -> None:
    db.execute(
        text(
            "update public.system_settings "
            "set value = jsonb_set(value, cast(:path as text[]), to_jsonb(cast(:v as int))) "
            "where key = :key"
        ),
        {"path": "{" + field + "}", "v": value, "key": key},
    )
    db.commit()
    clear_settings_cache()


def _own_child(db: Session, parent: ParentAccount, *, name: str = "王小明") -> Student:
    student = make_student(db, name=name)
    make_guardian(db, student, parent=parent)
    return student


def _withdraw(db: Session, student: Student) -> None:
    student.status = "withdrawn"
    student.withdrawn_on = date(2026, 8, 31)
    db.flush()


# --- BACKEND-441：GET /api/parent/pickup/requests/today -------------------------------------------

_TODAY_URL = "/api/parent/pickup/requests/today"


def test_parent_pickup_today_success(
    parent_client: ParentClientFactory, db_session: Session
) -> None:
    client, parent = parent_client()
    ming = _own_child(db_session, parent)
    make_pickup_request(db_session, ming, service_date=_TODAY, requested_by=parent.id)
    make_pickup_request(
        db_session, ming, service_date=_TODAY - timedelta(days=1), status="completed"
    )
    db_session.commit()

    resp = client.get(_TODAY_URL)

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["student_name"] == "王小明"
    assert body[0]["student_id"] == str(ming.id)
    assert body[0]["status"] == "pending"
    assert body[0]["can_cancel"] is True
    assert "replied_by_name" not in body[0]


def test_parent_pickup_today_422(
    parent_client: ParentClientFactory, assert_error: AssertError
) -> None:
    client, _ = parent_client()

    # GET 無 body / 無 query model：未知 query 不影響
    assert client.get(_TODAY_URL, params={"foo": "bar"}).status_code == 200
    assert_error(client.get(f"{_TODAY_URL}/x"), 404, "not_found")


def test_parent_pickup_today_401(
    api_client: TestClient, staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    assert_error(api_client.get(_TODAY_URL), 401, "unauthenticated")
    staff, _ = staff_client(permissions=["pickup:read"])
    assert_error(staff.get(_TODAY_URL), 401, "unauthenticated")


def test_parent_pickup_today_idor(parent_client: ParentClientFactory, db_session: Session) -> None:
    client_a, parent_a = parent_client()
    _, parent_b = parent_client()
    ming = _own_child(db_session, parent_a)
    hua = _own_child(db_session, parent_b, name="陳小華")
    make_pickup_request(db_session, ming, service_date=_TODAY)
    make_pickup_request(db_session, hua, service_date=_TODAY)
    db_session.commit()

    resp = client_a.get(_TODAY_URL)

    assert [r["student_name"] for r in resp.json()] == ["王小明"]
    assert "陳小華" not in resp.text


def test_parent_pickup_today_empty(parent_client: ParentClientFactory, db_session: Session) -> None:
    client, parent = parent_client()
    _own_child(db_session, parent)
    db_session.commit()

    resp = client.get(_TODAY_URL)

    assert resp.status_code == 200
    assert resp.json() == []


# --- BACKEND-444：GET /api/parent/children/{id}/pickup-persons ------------------------------------


def _persons_url(student_id: object) -> str:
    return f"/api/parent/children/{student_id}/pickup-persons"


def test_parent_pickup_persons_list_success(
    parent_client: ParentClientFactory, db_session: Session
) -> None:
    client, parent = parent_client()
    ming = _own_child(db_session, parent)
    person = make_pickup_person(db_session, ming, name="李阿姨")
    db_session.commit()

    resp = client.get(_persons_url(ming.id))

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["id"] == str(person.id)
    assert body[0]["name"] == "李阿姨"
    assert body[0]["photo_url"] is None
    assert set(body[0]) == {
        "id",
        "student_id",
        "name",
        "relation",
        "phone",
        "photo_url",
        "created_at",
    }


def test_parent_pickup_persons_list_422(
    parent_client: ParentClientFactory, assert_error: AssertError
) -> None:
    client, _ = parent_client()

    assert_error(client.get(_persons_url("abc")), 422, "validation_error")


def test_parent_pickup_persons_list_401(
    api_client: TestClient, staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    assert_error(api_client.get(_persons_url(uuid4())), 401, "unauthenticated")
    staff, _ = staff_client(permissions=["pickup:read"])
    assert_error(staff.get(_persons_url(uuid4())), 401, "unauthenticated")


def test_parent_pickup_persons_list_idor(
    parent_client: ParentClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    client_a, _ = parent_client()
    _, parent_b = parent_client()
    hua = _own_child(db_session, parent_b, name="陳小華")
    make_pickup_person(db_session, hua, name="B 的接送人")
    db_session.commit()

    theirs = client_a.get(_persons_url(hua.id))
    missing = client_a.get(_persons_url(uuid4()))

    assert_error(theirs, 404, "student_not_found")
    assert theirs.json() == missing.json()
    assert "B 的接送人" not in theirs.text


def test_parent_pickup_persons_list_archived_excluded(
    parent_client: ParentClientFactory, db_session: Session, fake_clock: FakeClock
) -> None:
    client, parent = parent_client()
    ming = _own_child(db_session, parent)
    make_pickup_person(db_session, ming, name="李阿姨")
    gone = make_pickup_person(db_session, ming, name="張伯伯", relation="伯伯")
    gone.archived_at = fake_clock.now()
    db_session.commit()

    resp = client.get(_persons_url(ming.id))

    assert [p["name"] for p in resp.json()] == ["李阿姨"]


# --- BACKEND-445：POST /api/parent/children/{id}/pickup-persons（multipart）---------------


def _post_person(
    client: TestClient,
    student_id: object,
    form: dict[str, Any] | None = None,
    *,
    photo: tuple[str, bytes, str] | None = None,
) -> Any:
    files = {"photo": photo} if photo is not None else None
    return client.post(_persons_url(student_id), data=form or _PERSON_FORM, files=files)


def test_parent_pickup_person_create_success(
    parent_client: ParentClientFactory, db_session: Session, fake_storage: FakeStorage
) -> None:
    client, parent = parent_client()
    ming = _own_child(db_session, parent)
    db_session.commit()

    resp = _post_person(client, ming.id, photo=("a.jpg", _JPEG, "image/jpeg"))

    assert resp.status_code == 201
    body = resp.json()
    assert (body["name"], body["relation"], body["phone"]) == ("李阿姨", "阿姨", "0912-000-101")
    assert body["student_id"] == str(ming.id)
    assert body["photo_url"] is not None
    assert body["photo_url"].startswith("https://storage.test/pickup-person-photos/")
    assert len(fake_storage.objects) == 1
    (((bucket, _path), content),) = fake_storage.objects.items()
    assert bucket == "pickup-person-photos"
    assert content == _JPEG
    # 不附照片也可以
    plain = _post_person(client, ming.id, {**_PERSON_FORM, "name": "王叔叔", "relation": "叔叔"})
    assert plain.status_code == 201
    assert plain.json()["photo_url"] is None
    # 同一交易內兩筆 created_at 相同，排序由 id 決定：只比對集合
    assert {p["name"] for p in client.get(_persons_url(ming.id)).json()} == {"李阿姨", "王叔叔"}


def test_parent_pickup_person_create_422(
    parent_client: ParentClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    client, parent = parent_client()
    ming = _own_child(db_session, parent)
    db_session.commit()

    bad_phone = _post_person(client, ming.id, {**_PERSON_FORM, "phone": "abc"})
    no_name = _post_person(client, ming.id, {"relation": "阿姨", "phone": "0912-000-101"})
    bad_path = _post_person(client, "abc")

    assert_error(bad_phone, 422, "validation_error")
    assert_error(no_name, 422, "validation_error")
    assert_error(bad_path, 422, "validation_error")
    assert client.get(_persons_url(ming.id)).json() == []


def test_parent_pickup_person_create_401(
    api_client: TestClient, staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    assert_error(_post_person(api_client, uuid4()), 401, "unauthenticated")
    staff, _ = staff_client(permissions=["pickup:write"])
    assert_error(_post_person(staff, uuid4()), 401, "unauthenticated")


def test_parent_pickup_person_create_idor(
    parent_client: ParentClientFactory,
    db_session: Session,
    assert_error: AssertError,
    fake_storage: FakeStorage,
) -> None:
    client_a, _ = parent_client()
    _, parent_b = parent_client()
    hua = _own_child(db_session, parent_b, name="陳小華")
    db_session.commit()

    theirs = _post_person(client_a, hua.id, photo=("a.jpg", _JPEG, "image/jpeg"))
    missing = _post_person(client_a, uuid4())

    assert_error(theirs, 404, "student_not_found")
    assert theirs.json() == missing.json()
    assert fake_storage.objects == {}
    assert db_session.query(PickupPerson).filter_by(student_id=hua.id).count() == 0


def test_parent_pickup_person_create_business(
    parent_client: ParentClientFactory,
    db_session: Session,
    assert_error: AssertError,
    fake_storage: FakeStorage,
) -> None:
    client, parent = parent_client()
    ming = _own_child(db_session, parent)
    gone = _own_child(db_session, parent, name="已退班")
    _withdraw(db_session, gone)
    db_session.commit()

    withdrawn = _post_person(client, gone.id)
    pdf = _post_person(client, ming.id, photo=("a.pdf", _PDF, "application/pdf"))
    # 上限：service 先檢查人數再驗檔案，故填滿後再打
    for n in range(10):
        make_pickup_person(db_session, ming, name=f"接送人{n}")
    _set_setting(db_session, "pickup.persons", "max_per_student", 10)
    limit = _post_person(client, ming.id)

    assert_error(withdrawn, 409, "student_not_active")
    assert_error(pdf, 415, "unsupported_file_type")
    assert_error(limit, 409, "pickup_person_limit_reached")
    assert limit.json()["error"]["details"] == {"max_per_student": 10}
    assert fake_storage.objects == {}


# --- BACKEND-446：DELETE /api/parent/pickup-persons/{id} ------------------------------------------


def _delete_url(person_id: object) -> str:
    return f"/api/parent/pickup-persons/{person_id}"


def test_parent_pickup_person_delete_success(
    parent_client: ParentClientFactory, db_session: Session
) -> None:
    client, parent = parent_client()
    ming = _own_child(db_session, parent)
    person = make_pickup_person(db_session, ming, name="李阿姨")
    keep = make_pickup_person(db_session, ming, name="王叔叔", relation="叔叔")
    db_session.commit()

    resp = client.delete(_delete_url(person.id))

    assert resp.status_code == 204
    assert resp.content == b""
    assert [p["id"] for p in client.get(_persons_url(ming.id)).json()] == [str(keep.id)]
    db_session.expire_all()
    assert person.archived_at is not None  # 軟刪除


def test_parent_pickup_person_delete_422(
    parent_client: ParentClientFactory, assert_error: AssertError
) -> None:
    client, _ = parent_client()

    assert_error(client.delete(_delete_url("abc")), 422, "validation_error")


def test_parent_pickup_person_delete_401(
    api_client: TestClient, staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    assert_error(api_client.delete(_delete_url(uuid4())), 401, "unauthenticated")
    staff, _ = staff_client(permissions=["pickup:write"])
    assert_error(staff.delete(_delete_url(uuid4())), 401, "unauthenticated")


def test_parent_pickup_person_delete_idor(
    parent_client: ParentClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    client_a, _ = parent_client()
    _, parent_b = parent_client()
    hua = _own_child(db_session, parent_b, name="陳小華")
    theirs_person = make_pickup_person(db_session, hua, name="B 的接送人")
    db_session.commit()

    theirs = client_a.delete(_delete_url(theirs_person.id))
    missing = client_a.delete(_delete_url(uuid4()))

    assert_error(theirs, 404, "pickup_person_not_found")
    assert theirs.json() == missing.json()
    db_session.expire_all()
    assert theirs_person.archived_at is None


def test_parent_pickup_person_delete_twice(
    parent_client: ParentClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    client, parent = parent_client()
    ming = _own_child(db_session, parent)
    person = make_pickup_person(db_session, ming)
    db_session.commit()

    assert client.delete(_delete_url(person.id)).status_code == 204
    assert_error(client.delete(_delete_url(person.id)), 404, "pickup_person_not_found")


# --- BACKEND-447：GET /api/parent/children/{id}/pickup-authorizations -----------------------------


def _auths_url(student_id: object) -> str:
    return f"/api/parent/children/{student_id}/pickup-authorizations"


def test_parent_pickup_auths_list_success(
    parent_client: ParentClientFactory, db_session: Session
) -> None:
    client, parent = parent_client()
    ming = _own_child(db_session, parent)
    auth = make_pickup_authorization(db_session, ming, service_date=_TODAY, code="123456")
    db_session.commit()

    resp = client.get(_auths_url(ming.id))

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["id"] == str(auth.id)
    assert body[0]["effective_status"] == "active"
    assert body[0]["code_last4"] == "3456"
    assert body[0]["proxy_name"] == "李阿姨"
    assert "code_hash" not in resp.text
    assert "123456" not in resp.text


def test_parent_pickup_auths_list_422(
    parent_client: ParentClientFactory, assert_error: AssertError
) -> None:
    client, _ = parent_client()

    assert_error(client.get(_auths_url("abc")), 422, "validation_error")


def test_parent_pickup_auths_list_401(
    api_client: TestClient, staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    assert_error(api_client.get(_auths_url(uuid4())), 401, "unauthenticated")
    staff, _ = staff_client(permissions=["pickup:read"])
    assert_error(staff.get(_auths_url(uuid4())), 401, "unauthenticated")


def test_parent_pickup_auths_list_idor(
    parent_client: ParentClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    client_a, _ = parent_client()
    _, parent_b = parent_client()
    hua = _own_child(db_session, parent_b, name="陳小華")
    make_pickup_authorization(db_session, hua, service_date=_TODAY, proxy_name="B 的代理人")
    db_session.commit()

    theirs = client_a.get(_auths_url(hua.id))
    missing = client_a.get(_auths_url(uuid4()))

    assert_error(theirs, 404, "student_not_found")
    assert theirs.json() == missing.json()
    assert "B 的代理人" not in theirs.text


def test_parent_pickup_auths_list_expired_display(
    parent_client: ParentClientFactory, db_session: Session
) -> None:
    client, parent = parent_client()
    ming = _own_child(db_session, parent)
    yesterday = make_pickup_authorization(db_session, ming, service_date=_TODAY - timedelta(days=1))
    today = make_pickup_authorization(db_session, ming, service_date=_TODAY, code="654321")
    db_session.commit()

    body = {a["id"]: a for a in client.get(_auths_url(ming.id)).json()}

    assert body[str(yesterday.id)]["status"] == "active"
    assert body[str(yesterday.id)]["effective_status"] == "expired"
    assert body[str(today.id)]["effective_status"] == "active"


# --- BACKEND-448：POST /api/parent/children/{id}/pickup-authorizations ----------------------------


def _proxy_body(service_date: date = _TODAY) -> dict[str, Any]:
    return {
        "service_date": service_date.isoformat(),
        "proxy_name": "李阿姨",
        "proxy_phone": "0912-000-101",
    }


def test_parent_pickup_auth_create_success(
    parent_client: ParentClientFactory, db_session: Session
) -> None:
    client, parent = parent_client()
    ming = _own_child(db_session, parent)
    db_session.commit()

    resp = client.post(_auths_url(ming.id), json=_proxy_body())

    assert resp.status_code == 201
    assert resp.headers["cache-control"] == "no-store"
    body = resp.json()
    assert set(body) == {"authorization", "code"}
    assert len(body["code"]) == 6
    assert body["code"].isdigit()
    authorization = body["authorization"]
    assert authorization["code_last4"] == body["code"][-4:]
    assert authorization["student_id"] == str(ming.id)
    assert authorization["proxy_name"] == "李阿姨"
    assert authorization["effective_status"] == "active"
    assert "code_hash" not in resp.text
    # 之後的列表不再出現明碼
    listed = client.get(_auths_url(ming.id))
    assert body["code"] not in listed.text
    assert listed.json()[0]["id"] == authorization["id"]


def test_parent_pickup_auth_create_422(
    parent_client: ParentClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    client, parent = parent_client()
    ming = _own_child(db_session, parent)
    person = make_pickup_person(db_session, ming)
    db_session.commit()
    _set_setting(db_session, "pickup.authorization", "max_days_ahead", 14)

    both_modes = client.post(
        _auths_url(ming.id), json={**_proxy_body(), "pickup_person_id": str(person.id)}
    )
    too_far = client.post(_auths_url(ming.id), json=_proxy_body(_TODAY + timedelta(days=15)))
    in_range = client.post(_auths_url(ming.id), json=_proxy_body(_TODAY + timedelta(days=14)))
    extra = client.post(_auths_url(ming.id), json={**_proxy_body(), "foo": 1})

    assert_error(both_modes, 422, "validation_error")
    assert_error(too_far, 422, "invalid_service_date")
    assert too_far.json()["error"]["details"] == {"max_days_ahead": 14}
    assert in_range.status_code == 201
    assert_error(extra, 422, "validation_error")


def test_parent_pickup_auth_create_401(
    api_client: TestClient, staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    assert_error(api_client.post(_auths_url(uuid4()), json=_proxy_body()), 401, "unauthenticated")
    staff, _ = staff_client(permissions=["pickup:write"])
    assert_error(staff.post(_auths_url(uuid4()), json=_proxy_body()), 401, "unauthenticated")


def test_parent_pickup_auth_create_idor(
    parent_client: ParentClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    client_a, parent_a = parent_client()
    _, parent_b = parent_client()
    ming = _own_child(db_session, parent_a)
    hua = _own_child(db_session, parent_b, name="陳小華")
    theirs_person = make_pickup_person(db_session, hua, name="B 的接送人")
    db_session.commit()

    theirs = client_a.post(_auths_url(hua.id), json=_proxy_body())
    missing = client_a.post(_auths_url(uuid4()), json=_proxy_body())
    theirs_person_resp = client_a.post(
        _auths_url(ming.id),
        json={"service_date": _TODAY.isoformat(), "pickup_person_id": str(theirs_person.id)},
    )
    unknown_person = client_a.post(
        _auths_url(ming.id),
        json={"service_date": _TODAY.isoformat(), "pickup_person_id": str(uuid4())},
    )

    assert_error(theirs, 404, "student_not_found")
    assert theirs.json() == missing.json()
    assert_error(theirs_person_resp, 404, "pickup_person_not_found")
    assert theirs_person_resp.json() == unknown_person.json()
    assert client_a.get(_auths_url(ming.id)).json() == []


def test_parent_pickup_auth_create_409(
    parent_client: ParentClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    client, parent = parent_client()
    ming = _own_child(db_session, parent)
    gone = _own_child(db_session, parent, name="已退班")
    _withdraw(db_session, gone)
    for code in ("111111", "222222", "333333"):
        make_pickup_authorization(db_session, ming, service_date=_TODAY, code=code)
    db_session.commit()
    _set_setting(db_session, "pickup.authorization", "max_active_per_day", 3)

    limit = client.post(_auths_url(ming.id), json=_proxy_body())
    withdrawn = client.post(_auths_url(gone.id), json=_proxy_body())

    assert_error(limit, 409, "authorization_limit_reached")
    assert_error(withdrawn, 409, "student_not_active")
    assert len(client.get(_auths_url(ming.id)).json()) == 3
