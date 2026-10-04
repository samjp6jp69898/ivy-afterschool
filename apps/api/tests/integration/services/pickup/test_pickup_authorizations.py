"""BACKEND-421 / 423：app/services/pickup/authorizations.py（家長端列表、後台核驗清單）。"""

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.crypto import derive_key
from app.core.storage import StorageError, build_object_path
from app.schemas.pickup import StaffAuthorizationListQuery, StaffAuthorizationOut
from app.services.pickup.authorizations import (
    list_authorizations_for_staff,
    list_child_authorizations,
)
from tests.support.factories import (
    make_class,
    make_pickup_authorization,
    make_pickup_person,
    make_staff,
    make_student,
)
from tests.support.fake_clock import FakeClock
from tests.support.fake_storage import FakeStorage

_TODAY = date(2026, 9, 10)
_CLOCK_NOW = datetime(2026, 9, 10, 2, 0, tzinfo=UTC)  # 台北 10:00
_URL_PREFIX = "https://storage.test/pickup-person-photos/"


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


def _mine(rows: list[StaffAuthorizationOut], ids: set[UUID]) -> list[StaffAuthorizationOut]:
    """只看本測試建立的授權，避免 DB 內其他資料干擾。"""
    return [row for row in rows if row.id in ids]


def test_list_child_authorizations(db_session: Session) -> None:
    clock = FakeClock(_CLOCK_NOW)
    ming = make_student(db_session)
    other = make_student(db_session, name="林小安")
    future = make_pickup_authorization(db_session, ming, service_date=date(2026, 9, 12))
    stale = make_pickup_authorization(db_session, ming, service_date=date(2026, 9, 9))
    done = make_pickup_authorization(
        db_session, ming, service_date=_TODAY, status="completed", code="654321"
    )
    boundary = make_pickup_authorization(db_session, ming, service_date=date(2026, 8, 11))
    cancelled = make_pickup_authorization(
        db_session, ming, service_date=date(2026, 9, 1), status="cancelled"
    )
    make_pickup_authorization(db_session, ming, service_date=date(2026, 8, 1))  # 超過 30 天
    make_pickup_authorization(db_session, other, service_date=_TODAY)  # 別的小孩

    rows = list_child_authorizations(db_session, ming.id, clock=clock)

    assert [(r.id, r.service_date, r.status, r.effective_status) for r in rows] == [
        (future.id, date(2026, 9, 12), "active", "active"),
        (done.id, _TODAY, "completed", "completed"),
        (stale.id, date(2026, 9, 9), "active", "expired"),
        (cancelled.id, date(2026, 9, 1), "cancelled", "cancelled"),
        (boundary.id, date(2026, 8, 11), "active", "expired"),
    ]
    assert rows[2].code_last4 == "3456"
    assert (rows[2].proxy_name, rows[2].proxy_phone, rows[2].student_id) == (
        "李阿姨",
        "0912-000-101",
        ming.id,
    )
    assert rows[1].verification_method == "code"
    assert rows[1].verified_at is not None


def test_list_child_authorizations_same_day_newest_first(db_session: Session) -> None:
    clock = FakeClock(_CLOCK_NOW)
    ming = make_student(db_session)
    older = make_pickup_authorization(db_session, ming, service_date=_TODAY, code="111111")
    newer = make_pickup_authorization(db_session, ming, service_date=_TODAY, code="222222")
    older.created_at = datetime(2026, 9, 8, tzinfo=UTC)
    newer.created_at = datetime(2026, 9, 9, tzinfo=UTC)
    db_session.flush()

    rows = list_child_authorizations(db_session, ming.id, clock=clock)

    assert [r.id for r in rows] == [newer.id, older.id]


def test_list_child_authorizations_range_follows_taipei_today(db_session: Session) -> None:
    # UTC 16:00 = 台北隔天 00:00：今天已是 9/11，今天 - 30 = 8/12
    clock = FakeClock(datetime(2026, 9, 10, 16, 0, tzinfo=UTC))
    ming = make_student(db_session)
    kept = make_pickup_authorization(db_session, ming, service_date=date(2026, 8, 12))
    make_pickup_authorization(db_session, ming, service_date=date(2026, 8, 11))

    rows = list_child_authorizations(db_session, ming.id, clock=clock)

    assert [(r.id, r.effective_status) for r in rows] == [(kept.id, "expired")]


def test_list_child_authorizations_no_hash(db_session: Session) -> None:
    clock = FakeClock(_CLOCK_NOW)
    ming = make_student(db_session)
    make_pickup_authorization(db_session, ming, service_date=_TODAY)

    rows = list_child_authorizations(db_session, ming.id, clock=clock)

    dumped = rows[0].model_dump()
    assert len(rows) == 1
    assert "code_hash" not in dumped
    assert "code_attempts" not in dumped


def test_staff_authorizations_list(db_session: Session) -> None:
    clock = FakeClock(_CLOCK_NOW)
    storage = FakeStorage()
    class_a = make_class(db_session, name="A班")
    ming = make_student(db_session, name="王小明", grade_level=3, class_=class_a)
    an = make_student(db_session, name="林小安", grade_level=4)
    hua = make_student(db_session, name="陳小華", grade_level=5)
    photo = build_object_path(uuid4(), "jpg")
    person = make_pickup_person(db_session, hua, photo_path=photo)
    locked = make_pickup_authorization(db_session, an, service_date=_TODAY, code_attempts=5)
    with_photo = make_pickup_authorization(
        db_session, hua, service_date=_TODAY, person=person, code="135790"
    )
    completed = make_pickup_authorization(db_session, ming, service_date=_TODAY, status="completed")
    yesterday = make_pickup_authorization(db_session, ming, service_date=_TODAY - timedelta(days=1))
    ids = {locked.id, with_photo.id, completed.id, yesterday.id}

    rows = _mine(
        list_authorizations_for_staff(
            db_session, StaffAuthorizationListQuery(), storage=storage, clock=clock
        ),
        ids,
    )

    # active 在前，再依學生姓名；昨天那筆不在預設日期內
    assert [r.id for r in rows] == [locked.id, with_photo.id, completed.id]
    by_id = {r.id: r for r in rows}
    assert by_id[locked.id].locked is True
    assert by_id[locked.id].code_attempts == 5
    assert by_id[with_photo.id].locked is False
    assert by_id[with_photo.id].photo_url == f"{_URL_PREFIX}{photo}?exp=300"
    assert by_id[with_photo.id].pickup_person_id == person.id
    assert by_id[with_photo.id].code_last4 == "5790"
    assert by_id[locked.id].photo_url is None
    student = by_id[completed.id].student
    assert (student.id, student.student_no, student.name, student.grade_level) == (
        ming.id,
        ming.student_no,
        "王小明",
        3,
    )
    assert (student.class_id, student.class_name) == (class_a.id, "A班")
    assert by_id[locked.id].student.class_name is None


def test_staff_authorizations_list_date_filter_and_photo_sign_error(db_session: Session) -> None:
    clock = FakeClock(_CLOCK_NOW)
    storage = FakeStorage()
    storage.sign_error = StorageError("S3 generate_presigned_url 失敗")
    ming = make_student(db_session)
    person = make_pickup_person(db_session, ming, photo_path=build_object_path(uuid4(), "jpg"))
    yesterday = make_pickup_authorization(
        db_session, ming, service_date=_TODAY - timedelta(days=1), person=person
    )
    make_pickup_authorization(db_session, ming, service_date=_TODAY)

    rows = _mine(
        list_authorizations_for_staff(
            db_session,
            StaffAuthorizationListQuery(date=_TODAY - timedelta(days=1)),
            storage=storage,
            clock=clock,
        ),
        {yesterday.id},
    )

    assert [(r.id, r.service_date, r.photo_url) for r in rows] == [
        (yesterday.id, _TODAY - timedelta(days=1), None)
    ]


def test_staff_authorizations_filter(db_session: Session) -> None:
    clock = FakeClock(_CLOCK_NOW)
    storage = FakeStorage()
    ming = make_student(db_session)
    teacher = make_staff(db_session, display_name="林老師")
    active = make_pickup_authorization(db_session, ming, service_date=_TODAY)
    done = make_pickup_authorization(db_session, ming, service_date=_TODAY, status="completed")
    done.verified_by = teacher.id
    db_session.flush()
    ids = {active.id, done.id}

    rows = _mine(
        list_authorizations_for_staff(
            db_session,
            StaffAuthorizationListQuery(status="completed"),
            storage=storage,
            clock=clock,
        ),
        ids,
    )

    assert [(r.id, r.status, r.verified_by_name, r.verification_method) for r in rows] == [
        (done.id, "completed", "林老師", "code")
    ]
    assert rows[0].verified_at is not None


def test_staff_authorizations_no_hash(db_session: Session) -> None:
    clock = FakeClock(_CLOCK_NOW)
    ming = make_student(db_session)
    auth = make_pickup_authorization(db_session, ming, service_date=_TODAY)

    rows = _mine(
        list_authorizations_for_staff(
            db_session, StaffAuthorizationListQuery(), storage=FakeStorage(), clock=clock
        ),
        {auth.id},
    )

    assert len(rows) == 1
    dumped = rows[0].model_dump()
    assert "code_hash" not in dumped
    assert dumped["code_last4"] == "3456"
