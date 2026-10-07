"""app/services/pickup/authorizations.py。

- BACKEND-421 / 423：家長端列表、後台核驗清單。
- BACKEND-420：create_authorization（產生接送碼，明碼只回傳一次）。
- BACKEND-424：load_verifiable_authorization（核銷前鎖定並檢查）。
- BACKEND-523：regenerate_code（家長重新產生接送碼）。
- BACKEND-425：complete_via_authorization（核銷後完成授權與接送請求）。
- BACKEND-426：verify_code（核對接送碼，連錯 5 次鎖定、原子累計）。
- BACKEND-427：confirm_visual_match（員工目視核對確認核銷，照片為輔助、寫 audit）。
- BACKEND-428：override_complete（主管強制完成代理接送，寫 audit）。
- BACKEND-555：authorization_out / staff_authorization_out（公開組裝函式；close_out 不再匯入
  私有名稱）。
"""

import ast
import inspect
import json
import logging
import re
import threading
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.encoders import jsonable_encoder
from sqlalchemy import Engine, func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.api.deps import CurrentParent, CurrentStaff
from app.core.config import get_settings
from app.core.crypto import derive_key
from app.core.errors import AppError
from app.core.request_meta import RequestMeta
from app.core.storage import StorageError, build_object_path
from app.core.tx_hooks import install_tx_hooks
from app.models.account import StaffUser
from app.models.attendance import StudentAttendance
from app.models.audit import AuditLog
from app.models.notifications import Notification
from app.models.parents import ParentAccount
from app.models.pickup import PickupAuthorization, PickupRequest
from app.notifications import outbox_jobs
from app.realtime import publish as publish_module
from app.realtime.publish import admin_topic_channel
from app.repositories.students import student_brief_map
from app.schemas.pickup import (
    PickupAuthorizationCreateIn,
    PickupAuthorizationOut,
    StaffAuthorizationListQuery,
    StaffAuthorizationOut,
    VisualMatchIn,
)
from app.services import student_service
from app.services.pickup import authorizations as authorizations_module
from app.services.pickup.authorizations import (
    VerifyOutcome,
    authorization_out,
    complete_via_authorization,
    confirm_visual_match,
    create_authorization,
    list_authorizations_for_staff,
    list_child_authorizations,
    load_verifiable_authorization,
    override_complete,
    regenerate_code,
    staff_authorization_out,
    verify_code,
)
from app.services.pickup.codes import pickup_code_matches
from app.services.settings_service import clear_settings_cache
from tests.integration.db.conftest import connect_owner
from tests.support.factories import (
    make_attendance,
    make_class,
    make_guardian,
    make_parent,
    make_pickup_authorization,
    make_pickup_person,
    make_pickup_request,
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
    assert by_id[locked.id].effective_status == "active"  # 今天的 active 還沒過期
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

    assert [(r.id, r.service_date, r.photo_url, r.effective_status) for r in rows] == [
        (yesterday.id, _TODAY - timedelta(days=1), None, "expired")
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


# --- BACKEND-420 create_authorization ---

Call = tuple[list[str], dict[str, Any]]


@pytest.fixture
def fresh_settings() -> Iterator[None]:
    clear_settings_cache()
    yield
    clear_settings_cache()


@pytest.fixture
def published(monkeypatch: pytest.MonkeyPatch) -> list[Call]:
    install_tx_hooks()
    calls: list[Call] = []

    def record(channels: list[str], message: dict[str, Any]) -> None:
        calls.append((list(channels), dict(message)))

    monkeypatch.setattr(publish_module, "publish_threadsafe", record)
    return calls


def _current_parent(parent: ParentAccount) -> CurrentParent:
    return CurrentParent(
        id=parent.id,
        line_user_id=parent.line_user_id,
        display_name=parent.display_name,
        token_version=parent.token_version,
    )


def _set_authorization_settings(session: Session, **values: int) -> None:
    for key, value in values.items():
        session.execute(
            text(
                "update public.system_settings "
                "set value = jsonb_set(value, cast(:path as text[]), to_jsonb(cast(:v as int))) "
                "where key = 'pickup.authorization'"
            ),
            {"path": "{" + key + "}", "v": value},
        )
    clear_settings_cache()


def _proxy(service_date: date) -> PickupAuthorizationCreateIn:
    return PickupAuthorizationCreateIn(
        service_date=service_date, proxy_name="李阿姨", proxy_phone="0912-000-101"
    )


def test_create_authorization_proxy(
    db_session: Session, fresh_settings: None, published: list[Call]
) -> None:
    clock = FakeClock(_CLOCK_NOW)
    ming = make_student(db_session)
    parent = make_parent(db_session)

    out = create_authorization(
        db_session, ming.id, _proxy(_TODAY), parent=_current_parent(parent), clock=clock
    )

    assert re.fullmatch(r"\d{6}", out.code)
    auth = out.authorization
    assert auth.code_last4 == out.code[-4:]
    assert (auth.status, auth.effective_status) == ("active", "active")
    assert (auth.student_id, auth.service_date) == (ming.id, _TODAY)
    assert (auth.proxy_name, auth.proxy_phone, auth.pickup_person_id) == (
        "李阿姨",
        "0912-000-101",
        None,
    )
    row = db_session.get(PickupAuthorization, auth.id)
    assert row is not None
    assert pickup_code_matches(out.code, row.code_hash) is True
    assert (row.created_by_parent_id, row.code_attempts) == (parent.id, 0)

    db_session.commit()
    [message] = [m for channels, m in published if channels == [admin_topic_channel("pickup")]]
    assert message["type"] == "pickup.authorization_updated"
    assert message["data"]["id"] == str(auth.id)
    assert message["data"]["code_last4"] == out.code[-4:]
    assert out.code not in str(message)
    assert "code_hash" not in message["data"]


def test_create_authorization_from_person(db_session: Session, fresh_settings: None) -> None:
    clock = FakeClock(_CLOCK_NOW)
    ming = make_student(db_session)
    person = make_pickup_person(db_session, ming, name="李阿姨", phone="0912-000-202")
    data = PickupAuthorizationCreateIn(
        service_date=_TODAY + timedelta(days=1), pickup_person_id=person.id
    )

    out = create_authorization(
        db_session, ming.id, data, parent=_current_parent(make_parent(db_session)), clock=clock
    )

    assert out.authorization.pickup_person_id == person.id
    assert (out.authorization.proxy_name, out.authorization.proxy_phone) == (
        "李阿姨",
        "0912-000-202",
    )


def test_create_authorization_no_plaintext_stored(
    db_session: Session, fresh_settings: None, caplog: pytest.LogCaptureFixture
) -> None:
    clock = FakeClock(_CLOCK_NOW)
    ming = make_student(db_session)
    caplog.set_level(logging.DEBUG)

    out = create_authorization(
        db_session,
        ming.id,
        _proxy(_TODAY),
        parent=_current_parent(make_parent(db_session)),
        clock=clock,
    )

    row = db_session.execute(
        text("select * from public.pickup_authorizations where id = :id"),
        {"id": out.authorization.id},
    ).one()
    values = [str(value) for value in row]
    assert out.code not in values
    assert row.code_hash != out.code
    assert out.code not in row.code_hash
    assert out.code not in caplog.text


def test_create_authorization_validation(db_session: Session, fresh_settings: None) -> None:
    clock = FakeClock(_CLOCK_NOW)
    ming = make_student(db_session)
    other = make_student(db_session, name="林小安")
    parent = _current_parent(make_parent(db_session))
    _set_authorization_settings(db_session, max_days_ahead=14)

    def create(data: PickupAuthorizationCreateIn) -> Any:
        return create_authorization(db_session, ming.id, data, parent=parent, clock=clock)

    for bad in (_TODAY + timedelta(days=15), _TODAY - timedelta(days=1)):
        with pytest.raises(AppError) as invalid:
            create(_proxy(bad))
        assert (invalid.value.status, invalid.value.code) == (422, "invalid_service_date")
    assert create(_proxy(_TODAY + timedelta(days=14))).authorization.status == "active"

    others_person = make_pickup_person(db_session, other)
    archived = make_pickup_person(db_session, ming)
    archived.archived_at = datetime(2026, 9, 1, tzinfo=UTC)
    db_session.flush()
    for person_id in (others_person.id, archived.id, uuid4()):
        with pytest.raises(AppError) as missing:
            create(PickupAuthorizationCreateIn(service_date=_TODAY, pickup_person_id=person_id))
        assert (missing.value.status, missing.value.code) == (404, "pickup_person_not_found")

    _set_authorization_settings(db_session, max_active_per_day=1)
    make_pickup_authorization(db_session, ming, service_date=_TODAY + timedelta(days=2))
    # 已取消的不計入上限
    make_pickup_authorization(
        db_session, ming, service_date=_TODAY + timedelta(days=3), status="cancelled"
    )
    assert create(_proxy(_TODAY + timedelta(days=3))).authorization.status == "active"
    with pytest.raises(AppError) as limit:
        create(_proxy(_TODAY + timedelta(days=2)))
    assert (limit.value.status, limit.value.code) == (409, "authorization_limit_reached")


@pytest.fixture
def owner_cleanup_students() -> Iterator[list[UUID]]:
    """committing 測試建立的學生 / 家長以 owner 連線刪除；排在 committing_db_session 之前
    （先 close session、truncate 授權表，再刪學生）。"""
    ids: list[UUID] = []
    yield ids
    with connect_owner() as conn:
        conn.execute("set lock_timeout = '5s'")
        for row_id in ids:
            conn.execute("delete from public.students where id = %s", (row_id,))
            conn.execute("delete from public.parent_accounts where id = %s", (row_id,))
        conn.commit()


def _active_count(session: Session, student_id: UUID) -> int:
    return session.execute(
        select(func.count())
        .select_from(PickupAuthorization)
        .where(PickupAuthorization.student_id == student_id, PickupAuthorization.status == "active")
    ).scalar_one()


@pytest.mark.cleanup_tables("pickup_authorizations")
def test_create_authorization_concurrent_limit(
    owner_cleanup_students: list[UUID],
    committing_db_session: Session,
    db_engine: Engine,
    fresh_settings: None,
) -> None:
    """預設上限 3、已有 2 筆：s1 建立第 3 筆未 commit 時 s2 被擋住，s1 commit 後 s2 得到 409。"""
    clock = FakeClock(_CLOCK_NOW)
    ming = make_student(committing_db_session)
    parent_row = make_parent(committing_db_session)
    for _ in range(2):
        make_pickup_authorization(committing_db_session, ming, service_date=_TODAY)
    committing_db_session.commit()
    owner_cleanup_students.extend([ming.id, parent_row.id])
    parent = _current_parent(parent_row)

    s1 = Session(bind=db_engine)
    s2 = Session(bind=db_engine)
    s2_done = threading.Event()
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            create_authorization(s2, ming.id, _proxy(_TODAY), parent=parent, clock=clock)
            s2.commit()
        except BaseException as exc:
            errors.append(exc)
        finally:
            s2_done.set()

    thread = threading.Thread(target=worker)
    try:
        create_authorization(s1, ming.id, _proxy(_TODAY), parent=parent, clock=clock)
        thread.start()
        assert not s2_done.wait(timeout=0.5), errors  # s1 尚未 commit：s2 被擋住
        s1.commit()
        thread.join(timeout=10)
    finally:
        s1.close()
        if thread.is_alive():
            thread.join(timeout=10)
        s2.close()

    [error] = errors
    assert isinstance(error, AppError)
    assert (error.status, error.code) == (409, "authorization_limit_reached")
    assert _active_count(committing_db_session, ming.id) == 3


# --- BACKEND-424 load_verifiable_authorization ---


def test_load_verifiable_authorization_errors(db_session: Session) -> None:
    clock = FakeClock(_CLOCK_NOW)
    ming = make_student(db_session)
    ok = make_pickup_authorization(db_session, ming, service_date=_TODAY)
    cancelled = make_pickup_authorization(db_session, ming, service_date=_TODAY, status="cancelled")
    completed = make_pickup_authorization(db_session, ming, service_date=_TODAY, status="completed")
    tomorrow = make_pickup_authorization(db_session, ming, service_date=_TODAY + timedelta(days=1))
    yesterday = make_pickup_authorization(db_session, ming, service_date=_TODAY - timedelta(days=1))
    locked = make_pickup_authorization(db_session, ming, service_date=_TODAY, code_attempts=5)

    def error_of(auth_id: UUID) -> tuple[int, str]:
        with pytest.raises(AppError) as exc:
            load_verifiable_authorization(db_session, auth_id, clock=clock)
        return exc.value.status, exc.value.code

    assert load_verifiable_authorization(db_session, ok.id, clock=clock).id == ok.id
    assert error_of(uuid4()) == (404, "pickup_authorization_not_found")
    assert error_of(cancelled.id) == (409, "authorization_not_active")
    assert error_of(completed.id) == (409, "authorization_not_active")
    assert error_of(tomorrow.id) == (409, "authorization_not_today")
    assert error_of(yesterday.id) == (409, "authorization_not_today")
    assert error_of(locked.id) == (409, "pickup_code_locked")


def test_load_verifiable_authorization_today_follows_taipei(db_session: Session) -> None:
    """UTC 9/9 16:30 = 台北 9/10 00:30：9/10 的授權已是今天。"""
    clock = FakeClock(datetime(2026, 9, 9, 16, 30, tzinfo=UTC))
    auth = make_pickup_authorization(db_session, make_student(db_session), service_date=_TODAY)

    assert load_verifiable_authorization(db_session, auth.id, clock=clock).id == auth.id


def test_load_verifiable_authorization_allow_locked(db_session: Session) -> None:
    clock = FakeClock(_CLOCK_NOW)
    ming = make_student(db_session)
    locked = make_pickup_authorization(db_session, ming, service_date=_TODAY, code_attempts=5)
    cancelled_locked = make_pickup_authorization(
        db_session, ming, service_date=_TODAY, code_attempts=5, status="cancelled"
    )

    loaded = load_verifiable_authorization(db_session, locked.id, clock=clock, allow_locked=True)

    assert (loaded.id, loaded.code_attempts) == (locked.id, 5)
    assert loaded.code_locked_at is not None
    # allow_locked 只放行鎖定，其他檢查照常
    with pytest.raises(AppError) as exc:
        load_verifiable_authorization(
            db_session, cancelled_locked.id, clock=clock, allow_locked=True
        )
    assert exc.value.code == "authorization_not_active"


@pytest.mark.cleanup_tables("pickup_authorizations")
def test_load_verifiable_authorization_locks_row(
    owner_cleanup_students: list[UUID], committing_db_session: Session, db_engine: Engine
) -> None:
    clock = FakeClock(_CLOCK_NOW)
    ming = make_student(committing_db_session)
    auth = make_pickup_authorization(committing_db_session, ming, service_date=_TODAY)
    committing_db_session.commit()
    owner_cleanup_students.append(ming.id)

    s1 = Session(bind=db_engine)
    s2 = Session(bind=db_engine)
    try:
        assert load_verifiable_authorization(s1, auth.id, clock=clock).id == auth.id
        with pytest.raises(OperationalError) as blocked:
            s2.execute(
                text(
                    "select id from public.pickup_authorizations where id = :id for update nowait"
                ),
                {"id": auth.id},
            )
        assert getattr(blocked.value.orig, "sqlstate", None) == "55P03"  # lock_not_available
        s2.rollback()

        s1.commit()
        # 釋放後可取得
        s2.execute(
            text("select id from public.pickup_authorizations where id = :id for update nowait"),
            {"id": auth.id},
        )
        s2.rollback()
    finally:
        s1.close()
        s2.close()


# --- BACKEND-523 regenerate_code ---

_META = RequestMeta(ip="203.0.113.5", user_agent="pytest", request_id=None)


def _parent_of(db: Session, *students: Any) -> CurrentParent:
    parent = make_parent(db)
    for student in students:
        make_guardian(db, student, parent=parent)
    return _current_parent(parent)


def _fresh(db: Session, auth_id: UUID) -> PickupAuthorization:
    return db.execute(
        select(PickupAuthorization)
        .where(PickupAuthorization.id == auth_id)
        .execution_options(populate_existing=True)
    ).scalar_one()


def test_regenerate_code_success(
    db_session: Session, published: list[Call], monkeypatch: pytest.MonkeyPatch
) -> None:
    # 固定新碼，避免亂數剛好產生舊碼（1/900000）讓「舊碼失效」的斷言不穩定
    monkeypatch.setattr(authorizations_module, "generate_pickup_code", lambda: "654321")
    clock = FakeClock(_CLOCK_NOW)
    ming = make_student(db_session)
    parent = _parent_of(db_session, ming)
    auth = make_pickup_authorization(
        db_session, ming, service_date=_TODAY, code="123456", code_attempts=5
    )

    out = regenerate_code(db_session, auth.id, parent=parent, meta=_META, clock=clock)

    assert out.code == "654321"
    row = _fresh(db_session, auth.id)
    assert pickup_code_matches(out.code, row.code_hash) is True
    assert pickup_code_matches("123456", row.code_hash) is False
    assert (row.code_attempts, row.code_locked_at) == (0, None)
    assert row.code_last4 == out.code[-4:] == out.authorization.code_last4
    assert (out.authorization.id, out.authorization.status) == (auth.id, "active")
    db_session.commit()
    [message] = [m for ch, m in published if ch == [admin_topic_channel("pickup")]]
    assert message["type"] == "pickup.authorization_updated"
    assert message["data"]["code_last4"] == out.code[-4:]
    assert out.code not in json.dumps(message)


def test_regenerate_code_state_errors(db_session: Session) -> None:
    clock = FakeClock(_CLOCK_NOW)
    ming = make_student(db_session)
    parent = _parent_of(db_session, ming)
    completed = make_pickup_authorization(db_session, ming, service_date=_TODAY, status="completed")
    cancelled = make_pickup_authorization(db_session, ming, service_date=_TODAY, status="cancelled")
    stale = make_pickup_authorization(db_session, ming, service_date=_TODAY - timedelta(days=1))
    future = make_pickup_authorization(db_session, ming, service_date=_TODAY + timedelta(days=1))

    for auth_id, code in (
        (completed.id, "authorization_not_active"),
        (cancelled.id, "authorization_not_active"),
        (stale.id, "authorization_expired"),
    ):
        with pytest.raises(AppError) as exc:
            regenerate_code(db_session, auth_id, parent=parent, meta=_META, clock=clock)
        assert (exc.value.status, exc.value.code) == (409, code)
    # 未來日期的 active 授權可以重新產生
    assert regenerate_code(db_session, future.id, parent=parent, meta=_META, clock=clock).code


def test_regenerate_code_withdrawn_student(db_session: Session) -> None:
    clock = FakeClock(_CLOCK_NOW)
    ming = make_student(db_session)
    parent = _parent_of(db_session, ming)
    auth = make_pickup_authorization(db_session, ming, service_date=_TODAY)
    ming.status = "withdrawn"
    ming.withdrawn_on = _TODAY
    db_session.flush()

    with pytest.raises(AppError) as exc:
        regenerate_code(db_session, auth.id, parent=parent, meta=_META, clock=clock)

    assert (exc.value.status, exc.value.code) == (409, "student_not_active")


def test_regenerate_code_idor(db_session: Session) -> None:
    clock = FakeClock(_CLOCK_NOW)
    ming = make_student(db_session)
    other = make_student(db_session, name="林小安")
    parent_a = _parent_of(db_session, ming)
    _parent_of(db_session, other)
    others_auth = make_pickup_authorization(db_session, other, service_date=_TODAY, code="654321")

    with pytest.raises(AppError) as idor:
        regenerate_code(db_session, others_auth.id, parent=parent_a, meta=_META, clock=clock)
    with pytest.raises(AppError) as missing:
        regenerate_code(db_session, uuid4(), parent=parent_a, meta=_META, clock=clock)

    assert (idor.value.status, idor.value.code) == (404, "pickup_authorization_not_found")
    assert (idor.value.status, idor.value.code, idor.value.message) == (
        missing.value.status,
        missing.value.code,
        missing.value.message,
    )
    assert pickup_code_matches("654321", _fresh(db_session, others_auth.id).code_hash) is True


def test_regenerate_code_audit(db_session: Session) -> None:
    clock = FakeClock(_CLOCK_NOW)
    ming = make_student(db_session)
    parent = _parent_of(db_session, ming)
    auth = make_pickup_authorization(
        db_session, ming, service_date=_TODAY, code="123456", code_attempts=5
    )
    old_hash = auth.code_hash

    out = regenerate_code(db_session, auth.id, parent=parent, meta=_META, clock=clock)

    [log] = db_session.execute(
        select(AuditLog).where(
            AuditLog.action == "pickup_authorization.regenerate_code",
            AuditLog.entity_id == str(auth.id),
        )
    ).scalars()
    assert (log.actor_type, log.actor_id, log.entity_type) == (
        "parent",
        parent.id,
        "pickup_authorization",
    )
    assert log.before == {"code_last4": "3456", "code_attempts": 5, "locked": True}
    assert log.after == {"code_last4": out.code[-4:], "code_attempts": 0, "locked": False}
    dumped = json.dumps([log.before, log.after])
    new_hash = _fresh(db_session, auth.id).code_hash
    for secret in (out.code, "123456", old_hash, new_hash):
        assert secret not in dumped


# --- BACKEND-425 complete_via_authorization ---


@pytest.fixture
def kick_off() -> Iterator[None]:
    """commit 後的 outbox kick 不實際派送（避免背景執行緒連 DB / LINE）。"""
    outbox_jobs.set_kick_mode("off")
    yield
    outbox_jobs.set_kick_mode("thread")


def _current_staff(staff: StaffUser) -> CurrentStaff:
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


def _completed_requests(db: Session, student_id: UUID) -> list[PickupRequest]:
    return list(
        db.execute(
            select(PickupRequest)
            .where(PickupRequest.student_id == student_id)
            .execution_options(populate_existing=True)
        ).scalars()
    )


def test_complete_via_authorization_closes_open_request(
    db_session: Session, kick_off: None, published: list[Call]
) -> None:
    clock = FakeClock(datetime(2026, 9, 10, 9, 0, tzinfo=UTC))  # 台北 17:00
    ming = make_student(db_session)
    _parent_of(db_session, ming)
    staff = make_staff(db_session, permissions=["pickup:operate"], display_name="林老師")
    make_attendance(db_session, ming, service_date=_TODAY, status="present")
    request = make_pickup_request(
        db_session, ming, service_date=_TODAY, status="arrived", reply_source="staff"
    )
    auth = make_pickup_authorization(db_session, ming, service_date=_TODAY, proxy_name="李阿姨")
    locked = load_verifiable_authorization(db_session, auth.id, clock=clock)

    out = complete_via_authorization(
        db_session, locked, "code", actor=_current_staff(staff), clock=clock
    )

    row = _fresh(db_session, auth.id)
    assert (row.status, row.verification_method, row.verified_by, row.verified_at) == (
        "completed",
        "code",
        staff.id,
        clock.now(),
    )
    [completed] = _completed_requests(db_session, ming.id)
    assert completed.id == request.id
    assert (
        completed.status,
        completed.completion_method,
        completed.picked_up_by_authorization_id,
    ) == (
        "completed",
        "code",
        auth.id,
    )
    assert (completed.completed_by, completed.completed_at) == (staff.id, clock.now())
    attendance = db_session.execute(
        select(StudentAttendance)
        .where(StudentAttendance.student_id == ming.id)
        .execution_options(populate_existing=True)
    ).scalar_one()
    assert (attendance.status, attendance.check_out_source) == ("left", "pickup")
    assert (out.authorization.id, out.authorization.status, out.authorization.verified_by_name) == (
        auth.id,
        "completed",
        "林老師",
    )
    assert (out.request.id, out.request.status, out.request.picked_up_by_name) == (
        request.id,
        "completed",
        "李阿姨",
    )
    db_session.commit()
    pushed = {m["type"] for ch, m in published if ch == [admin_topic_channel("pickup")]}
    assert pushed == {"pickup.request_updated", "pickup.authorization_updated"}


def test_complete_via_authorization_creates_proxy_request(
    db_session: Session, kick_off: None
) -> None:
    clock = FakeClock(datetime(2026, 9, 10, 9, 0, tzinfo=UTC))
    ming = make_student(db_session)
    staff = make_staff(db_session, permissions=["pickup:operate", "pickup:override"])
    # 今天只有終態請求：不算進行中
    old = make_pickup_request(db_session, ming, service_date=_TODAY, status="cancelled")
    auth = make_pickup_authorization(db_session, ming, service_date=_TODAY, code_attempts=5)
    locked = load_verifiable_authorization(db_session, auth.id, clock=clock, allow_locked=True)

    out = complete_via_authorization(
        db_session, locked, "override", actor=_current_staff(staff), clock=clock
    )

    created = [r for r in _completed_requests(db_session, ming.id) if r.id != old.id]
    assert len(created) == 1
    new = created[0]
    assert (new.source, new.status, new.requested_by_type, new.requested_by_id) == (
        "proxy",
        "completed",
        "staff",
        staff.id,
    )
    assert (
        new.completion_method,
        new.picked_up_by_authorization_id,
        new.homework_status_at_request,
    ) == (
        "override",
        auth.id,
        "not_started",
    )
    assert new.arrived_at == new.completed_at == clock.now()
    assert out.request.id == new.id


def test_complete_via_authorization_notifies(db_session: Session, kick_off: None) -> None:
    clock = FakeClock(datetime(2026, 9, 10, 9, 0, tzinfo=UTC))
    ming = make_student(db_session)
    parent = _parent_of(db_session, ming)
    staff = make_staff(db_session, permissions=["pickup:operate"])
    auth = make_pickup_authorization(db_session, ming, service_date=_TODAY, proxy_name="李阿姨")
    locked = load_verifiable_authorization(db_session, auth.id, clock=clock)

    out = complete_via_authorization(
        db_session, locked, "visual_match", actor=_current_staff(staff), clock=clock
    )

    [row] = db_session.execute(
        select(Notification).where(
            Notification.event == "pickup.completed",
            Notification.payload["request_id"].astext == str(out.request.id),
        )
    ).scalars()
    assert (row.recipient_id, row.payload["picked_up_by"], row.payload["time"]) == (
        parent.id,
        "代理人 李阿姨",
        "17:00",
    )


# --- BACKEND-426 verify_code ---

_WRONG_CODE = "000000"


@pytest.fixture
def owner_cleanup_rows() -> Iterator[list[tuple[str, UUID]]]:
    """committing 測試建立的學生 / 員工以 owner 連線刪除；排在 committing_db_session 之前
    （先 close session、truncate 授權表，再刪人）。員工一律用 seed 系統角色，不建自訂角色。"""
    rows: list[tuple[str, UUID]] = []
    yield rows
    with connect_owner() as conn:
        conn.execute("set lock_timeout = '5s'")
        for table, row_id in rows:
            conn.execute(f"delete from public.{table} where id = %s", (row_id,))  # noqa: S608
        conn.commit()


def _attendance_of(db: Session, student_id: UUID) -> StudentAttendance:
    return db.execute(
        select(StudentAttendance)
        .where(StudentAttendance.student_id == student_id)
        .execution_options(populate_existing=True)
    ).scalar_one()


def test_verify_code_success(db_session: Session, kick_off: None) -> None:
    clock = FakeClock(datetime(2026, 9, 10, 9, 0, tzinfo=UTC))  # 台北 17:00
    ming = make_student(db_session)
    parent = _parent_of(db_session, ming)
    staff = make_staff(db_session, permissions=["pickup:operate"], display_name="林老師")
    make_attendance(db_session, ming, service_date=_TODAY, status="present")
    auth = make_pickup_authorization(
        db_session, ming, service_date=_TODAY, code="123456", proxy_name="李阿姨"
    )

    # 家長轉告時常帶空白：正規化後比對
    outcome = verify_code(db_session, auth.id, "123 456", actor=_current_staff(staff), clock=clock)

    assert (outcome.ok, outcome.locked, outcome.remaining_attempts) == (True, False, 5)
    assert outcome.result is not None
    result = outcome.result
    assert (result.authorization.id, result.authorization.status) == (auth.id, "completed")
    assert result.authorization.verification_method == "code"
    assert result.authorization.verified_by_name == "林老師"
    assert (result.request.status, result.request.completion_method, result.request.source) == (
        "completed",
        "code",
        "proxy",
    )
    assert result.request.picked_up_by_name == "李阿姨"
    row = _fresh(db_session, auth.id)
    assert (row.status, row.verification_method, row.verified_by, row.verified_at) == (
        "completed",
        "code",
        staff.id,
        clock.now(),
    )
    assert (row.code_attempts, row.code_locked_at) == (0, None)
    attendance = _attendance_of(db_session, ming.id)
    assert (attendance.status, attendance.check_out_source, attendance.check_out_at) == (
        "left",
        "pickup",
        clock.now(),
    )
    [notification] = db_session.execute(
        select(Notification).where(
            Notification.event == "pickup.completed",
            Notification.payload["request_id"].astext == str(result.request.id),
        )
    ).scalars()
    assert (notification.recipient_id, notification.payload["picked_up_by"]) == (
        parent.id,
        "代理人 李阿姨",
    )


def test_verify_code_mismatch_counts(db_session: Session, kick_off: None) -> None:
    clock = FakeClock(_CLOCK_NOW)
    ming = make_student(db_session)
    _parent_of(db_session, ming)
    staff = make_staff(db_session, permissions=["pickup:operate"])
    make_attendance(db_session, ming, service_date=_TODAY, status="present")
    auth = make_pickup_authorization(db_session, ming, service_date=_TODAY, code="123456")

    outcome = verify_code(
        db_session, auth.id, _WRONG_CODE, actor=_current_staff(staff), clock=clock
    )

    assert outcome == VerifyOutcome(ok=False, result=None, remaining_attempts=4, locked=False)
    row = _fresh(db_session, auth.id)
    assert (row.status, row.code_attempts, row.code_locked_at, row.verified_at) == (
        "active",
        1,
        None,
        None,
    )
    # 錯碼不核銷：沒有請求、出勤不動、沒有通知
    assert _completed_requests(db_session, ming.id) == []
    assert _attendance_of(db_session, ming.id).status == "present"
    assert (
        db_session.execute(
            select(func.count())
            .select_from(Notification)
            .where(Notification.payload["student_id"].astext == str(ming.id))
        ).scalar_one()
        == 0
    )


def test_verify_code_locks_on_fifth(
    db_session: Session, kick_off: None, caplog: pytest.LogCaptureFixture
) -> None:
    clock = FakeClock(_CLOCK_NOW)
    ming = make_student(db_session)
    staff = make_staff(db_session, permissions=["pickup:operate"])
    auth = make_pickup_authorization(
        db_session, ming, service_date=_TODAY, code="123456", code_attempts=4
    )
    caplog.set_level(logging.DEBUG)

    outcome = verify_code(
        db_session, auth.id, _WRONG_CODE, actor=_current_staff(staff), clock=clock
    )

    assert outcome == VerifyOutcome(ok=False, result=None, remaining_attempts=0, locked=True)
    row = _fresh(db_session, auth.id)
    assert (row.status, row.code_attempts, row.code_locked_at) == ("active", 5, clock.now())
    [warning] = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert str(auth.id) in warning.getMessage()
    # 明碼（輸入的與正確的）都不進 log
    assert _WRONG_CODE not in caplog.text
    assert "123456" not in caplog.text


def test_verify_code_locked_rejects_correct(db_session: Session, kick_off: None) -> None:
    clock = FakeClock(_CLOCK_NOW)
    ming = make_student(db_session)
    staff = make_staff(db_session, permissions=["pickup:operate"])
    auth = make_pickup_authorization(
        db_session, ming, service_date=_TODAY, code="123456", code_attempts=5
    )

    with pytest.raises(AppError) as exc:
        verify_code(db_session, auth.id, "123456", actor=_current_staff(staff), clock=clock)

    assert (exc.value.status, exc.value.code) == (409, "pickup_code_locked")
    row = _fresh(db_session, auth.id)
    assert (row.status, row.code_attempts, row.verified_at, row.verification_method) == (
        "active",
        5,
        None,
        None,
    )
    assert row.code_locked_at is not None
    assert _completed_requests(db_session, ming.id) == []


def _run_concurrently(workers: list[Callable[[], Any]], *, join_timeout: float) -> list[Any]:
    """每個 worker 一條執行緒；回傳值或例外依 worker 順序收集。"""
    results: list[Any] = [None] * len(workers)

    def run(index: int) -> None:
        try:
            results[index] = workers[index]()
        except BaseException as exc:  # 例外是測試要斷言的結果
            results[index] = exc

    threads = [threading.Thread(target=run, args=(i,)) for i in range(len(workers))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=join_timeout)
    assert not any(t.is_alive() for t in threads), "有 worker 在 join_timeout 內沒結束"
    return results


@pytest.mark.cleanup_tables("pickup_authorizations")
def test_verify_code_concurrent_attempts(
    owner_cleanup_rows: list[tuple[str, UUID]], committing_db_session: Session, db_engine: Engine
) -> None:
    """6 條連線同時錯碼：FOR UPDATE 序列化 → 累計 1..5、第 5 次鎖定，第 6 條看到鎖定得 409。"""
    clock = FakeClock(_CLOCK_NOW)
    ming = make_student(committing_db_session)
    teacher = make_staff(committing_db_session, role_code="tutor")
    auth = make_pickup_authorization(
        committing_db_session, ming, service_date=_TODAY, code="123456"
    )
    committing_db_session.commit()
    owner_cleanup_rows.extend([("students", ming.id), ("staff_users", teacher.id)])
    actor = _current_staff(teacher)
    count = 6
    barrier = threading.Barrier(count)

    def attempt() -> VerifyOutcome:
        session = Session(bind=db_engine)
        try:
            session.execute(text("set local lock_timeout = '10s'"))
            barrier.wait(timeout=10)
            outcome = verify_code(session, auth.id, _WRONG_CODE, actor=actor, clock=clock)
            session.commit()
            return outcome
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()

    results = _run_concurrently([attempt] * count, join_timeout=30)

    outcomes = [r for r in results if isinstance(r, VerifyOutcome)]
    errors = [r for r in results if not isinstance(r, VerifyOutcome)]
    assert len(outcomes) == 5
    assert all((o.ok, o.result) == (False, None) for o in outcomes)
    assert sorted(o.remaining_attempts for o in outcomes) == [0, 1, 2, 3, 4]
    assert [o.remaining_attempts for o in outcomes if o.locked] == [0]  # 恰一次鎖定轉換
    [error] = errors
    assert isinstance(error, AppError)
    assert (error.status, error.code) == (409, "pickup_code_locked")
    row = _fresh(committing_db_session, auth.id)
    assert (row.status, row.code_attempts, row.code_locked_at) == ("active", 5, clock.now())


@pytest.mark.cleanup_tables("pickup_authorizations")
def test_verify_code_concurrent_attempts_without_row_lock(
    owner_cleanup_rows: list[tuple[str, UUID]],
    committing_db_session: Session,
    db_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """模擬未來有路徑漏鎖（載入不帶 FOR UPDATE）：兩條連線都讀到 code_attempts=4 再同時錯碼，
    單一語句的 WHERE code_locked_at IS NULL + 原子 +1 仍只讓一條鎖定，另一條 409，code_attempts
    不超過 5。"""
    clock = FakeClock(_CLOCK_NOW)
    ming = make_student(committing_db_session)
    teacher = make_staff(committing_db_session, role_code="tutor")
    auth = make_pickup_authorization(
        committing_db_session, ming, service_date=_TODAY, code="123456", code_attempts=4
    )
    committing_db_session.commit()
    owner_cleanup_rows.extend([("students", ming.id), ("staff_users", teacher.id)])
    actor = _current_staff(teacher)
    barrier = threading.Barrier(2)

    def load_without_lock(
        session: Session, auth_id: UUID, *, clock: FakeClock, allow_locked: bool = False
    ) -> PickupAuthorization:
        loaded = session.execute(
            select(PickupAuthorization)
            .where(PickupAuthorization.id == auth_id)
            .execution_options(populate_existing=True)
        ).scalar_one()
        barrier.wait(timeout=10)  # 兩邊都讀完舊值（4、未鎖定）才繼續
        return loaded

    monkeypatch.setattr(authorizations_module, "load_verifiable_authorization", load_without_lock)

    def attempt() -> VerifyOutcome:
        session = Session(bind=db_engine)
        try:
            session.execute(text("set local lock_timeout = '10s'"))
            outcome = verify_code(session, auth.id, _WRONG_CODE, actor=actor, clock=clock)
            session.commit()
            return outcome
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()

    results = _run_concurrently([attempt, attempt], join_timeout=30)

    outcomes = [r for r in results if isinstance(r, VerifyOutcome)]
    errors = [r for r in results if not isinstance(r, VerifyOutcome)]
    assert outcomes == [VerifyOutcome(ok=False, result=None, remaining_attempts=0, locked=True)]
    [error] = errors
    assert isinstance(error, AppError)
    assert (error.status, error.code) == (409, "pickup_code_locked")
    row = _fresh(committing_db_session, auth.id)
    assert (row.status, row.code_attempts, row.code_locked_at) == ("active", 5, clock.now())


# --- BACKEND-427 confirm_visual_match ---


def _audit_logs(db: Session, action: str, entity_id: UUID) -> list[AuditLog]:
    return list(
        db.execute(
            select(AuditLog).where(AuditLog.action == action, AuditLog.entity_id == str(entity_id))
        ).scalars()
    )


def test_confirm_visual_match_with_photo(db_session: Session, kick_off: None) -> None:
    clock = FakeClock(datetime(2026, 9, 10, 9, 0, tzinfo=UTC))  # 台北 17:00
    ming = make_student(db_session)
    _parent_of(db_session, ming)
    staff = make_staff(db_session, permissions=["pickup:operate"], display_name="林老師")
    make_attendance(db_session, ming, service_date=_TODAY, status="present")
    request = make_pickup_request(db_session, ming, service_date=_TODAY, status="arrived")
    person = make_pickup_person(
        db_session, ming, name="李阿姨", photo_path=build_object_path(uuid4(), "jpg")
    )
    auth = make_pickup_authorization(db_session, ming, service_date=_TODAY, person=person)

    result = confirm_visual_match(
        db_session, auth.id, None, actor=_current_staff(staff), meta=_META, clock=clock
    )

    assert (result.authorization.id, result.authorization.status) == (auth.id, "completed")
    assert result.authorization.verification_method == "visual_match"
    assert result.authorization.verified_by_name == "林老師"
    assert (result.request.id, result.request.status, result.request.completion_method) == (
        request.id,
        "completed",
        "visual_match",
    )
    assert result.request.picked_up_by_name == "李阿姨"
    row = _fresh(db_session, auth.id)
    assert (row.status, row.verification_method, row.verified_by, row.verified_at) == (
        "completed",
        "visual_match",
        staff.id,
        clock.now(),
    )
    assert _attendance_of(db_session, ming.id).status == "left"
    [log] = _audit_logs(db_session, "pickup.visual_match", auth.id)
    assert (log.actor_type, log.actor_id, log.entity_type, log.ip) == (
        "staff",
        staff.id,
        "pickup_authorization",
        "203.0.113.5",
    )
    assert log.before == {"status": "active"}
    assert log.after == {"status": "completed", "has_photo": True, "note": None}
    assert log.after["has_photo"] is True


def test_confirm_visual_match_without_photo(db_session: Session, kick_off: None) -> None:
    clock = FakeClock(_CLOCK_NOW)
    ming = make_student(db_session)
    an = make_student(db_session, name="林小安")
    staff = make_staff(db_session, permissions=["pickup:operate"])
    # 一次性代理人（沒有常用接送人）
    one_off = make_pickup_authorization(db_session, ming, service_date=_TODAY, proxy_name="王叔叔")
    # 常用接送人但未上傳照片
    no_photo_person = make_pickup_person(db_session, an, photo_path=None)
    no_photo = make_pickup_authorization(
        db_session, an, service_date=_TODAY, person=no_photo_person, code="654321"
    )

    first = confirm_visual_match(
        db_session,
        one_off.id,
        VisualMatchIn(note="已核對身分證"),
        actor=_current_staff(staff),
        meta=_META,
        clock=clock,
    )
    second = confirm_visual_match(
        db_session,
        no_photo.id,
        VisualMatchIn(),
        actor=_current_staff(staff),
        meta=_META,
        clock=clock,
    )

    assert (first.authorization.status, first.authorization.verification_method) == (
        "completed",
        "visual_match",
    )
    assert (first.request.status, first.request.source, first.request.picked_up_by_name) == (
        "completed",
        "proxy",
        "王叔叔",
    )
    [log] = _audit_logs(db_session, "pickup.visual_match", one_off.id)
    assert log.after == {"status": "completed", "has_photo": False, "note": "已核對身分證"}
    assert log.before == {"status": "active"}
    assert (second.authorization.status, second.authorization.pickup_person_id) == (
        "completed",
        no_photo_person.id,
    )
    [log2] = _audit_logs(db_session, "pickup.visual_match", no_photo.id)
    assert log2.after == {"status": "completed", "has_photo": False, "note": None}


def test_confirm_visual_match_locked(db_session: Session, kick_off: None) -> None:
    clock = FakeClock(_CLOCK_NOW)
    ming = make_student(db_session)
    staff = make_staff(db_session, permissions=["pickup:operate", "pickup:override"])
    auth = make_pickup_authorization(db_session, ming, service_date=_TODAY, code_attempts=5)

    with pytest.raises(AppError) as exc:
        confirm_visual_match(
            db_session,
            auth.id,
            VisualMatchIn(note="已核對身分證"),
            actor=_current_staff(staff),
            meta=_META,
            clock=clock,
        )

    assert (exc.value.status, exc.value.code) == (409, "pickup_code_locked")
    row = _fresh(db_session, auth.id)
    assert (row.status, row.verified_at, row.verification_method, row.code_attempts) == (
        "active",
        None,
        None,
        5,
    )
    assert _audit_logs(db_session, "pickup.visual_match", auth.id) == []
    assert _completed_requests(db_session, ming.id) == []


def test_confirm_visual_match_not_today(db_session: Session, kick_off: None) -> None:
    clock = FakeClock(_CLOCK_NOW)
    ming = make_student(db_session)
    staff = make_staff(db_session, permissions=["pickup:operate"])
    tomorrow = make_pickup_authorization(db_session, ming, service_date=_TODAY + timedelta(days=1))
    completed = make_pickup_authorization(
        db_session, ming, service_date=_TODAY, status="completed", code="654321"
    )

    with pytest.raises(AppError) as not_today:
        confirm_visual_match(
            db_session, tomorrow.id, None, actor=_current_staff(staff), meta=_META, clock=clock
        )
    with pytest.raises(AppError) as not_active:
        confirm_visual_match(
            db_session, completed.id, None, actor=_current_staff(staff), meta=_META, clock=clock
        )

    assert (not_today.value.status, not_today.value.code) == (409, "authorization_not_today")
    assert (not_active.value.status, not_active.value.code) == (409, "authorization_not_active")
    assert _fresh(db_session, tomorrow.id).status == "active"
    assert _audit_logs(db_session, "pickup.visual_match", tomorrow.id) == []


# --- BACKEND-428 override_complete ---


def test_override_complete_locked(db_session: Session, kick_off: None) -> None:
    clock = FakeClock(datetime(2026, 9, 10, 9, 0, tzinfo=UTC))  # 台北 17:00
    ming = make_student(db_session)
    _parent_of(db_session, ming)
    supervisor = make_staff(
        db_session, permissions=["pickup:operate", "pickup:override"], display_name="陳主任"
    )
    make_attendance(db_session, ming, service_date=_TODAY, status="present")
    auth = make_pickup_authorization(
        db_session, ming, service_date=_TODAY, code_attempts=5, proxy_name="李阿姨"
    )

    result = override_complete(
        db_session,
        auth.id,
        "已核對身分證",
        actor=_current_staff(supervisor),
        meta=_META,
        clock=clock,
    )

    assert (result.authorization.id, result.authorization.status) == (auth.id, "completed")
    assert result.authorization.verification_method == "override"
    assert (result.authorization.locked, result.authorization.code_attempts) == (True, 5)
    assert result.authorization.verified_by_name == "陳主任"
    assert (result.request.status, result.request.completion_method, result.request.source) == (
        "completed",
        "override",
        "proxy",
    )
    assert result.request.picked_up_by_name == "李阿姨"
    row = _fresh(db_session, auth.id)
    assert (row.status, row.verification_method, row.verified_by, row.verified_at) == (
        "completed",
        "override",
        supervisor.id,
        clock.now(),
    )
    # 強制完成不解鎖、不重設連錯次數
    assert row.code_attempts == 5
    assert row.code_locked_at is not None
    assert _attendance_of(db_session, ming.id).status == "left"
    [log] = _audit_logs(db_session, "pickup.override_complete", auth.id)
    assert (log.actor_type, log.actor_id, log.entity_type, log.ip) == (
        "staff",
        supervisor.id,
        "pickup_authorization",
        "203.0.113.5",
    )
    assert log.before == {"status": "active", "code_attempts": 5, "locked": True}
    assert log.before["locked"] is True
    assert log.after == {"status": "completed", "note": "已核對身分證"}


def test_override_complete_active_unlocked(db_session: Session, kick_off: None) -> None:
    clock = FakeClock(_CLOCK_NOW)
    ming = make_student(db_session)
    supervisor = make_staff(db_session, permissions=["pickup:override"])
    auth = make_pickup_authorization(db_session, ming, service_date=_TODAY, code_attempts=2)

    result = override_complete(
        db_session,
        auth.id,
        "家長來電確認",
        actor=_current_staff(supervisor),
        meta=_META,
        clock=clock,
    )

    assert (result.authorization.status, result.authorization.verification_method) == (
        "completed",
        "override",
    )
    [log] = _audit_logs(db_session, "pickup.override_complete", auth.id)
    assert log.before == {"status": "active", "code_attempts": 2, "locked": False}
    assert log.after == {"status": "completed", "note": "家長來電確認"}


def test_override_complete_forbidden(db_session: Session, kick_off: None) -> None:
    clock = FakeClock(_CLOCK_NOW)
    ming = make_student(db_session)
    operator = make_staff(db_session, permissions=["pickup:operate"])
    auth = make_pickup_authorization(db_session, ming, service_date=_TODAY, code_attempts=5)

    with pytest.raises(AppError) as exc:
        override_complete(
            db_session,
            auth.id,
            "已核對身分證",
            actor=_current_staff(operator),
            meta=_META,
            clock=clock,
        )

    assert (exc.value.status, exc.value.code) == (403, "permission_denied")
    assert exc.value.details == {"required": ["pickup:override"]}
    row = _fresh(db_session, auth.id)
    assert (row.status, row.verified_at, row.verification_method, row.code_attempts) == (
        "active",
        None,
        None,
        5,
    )
    assert _audit_logs(db_session, "pickup.override_complete", auth.id) == []
    assert _completed_requests(db_session, ming.id) == []


def test_override_complete_not_today(db_session: Session, kick_off: None) -> None:
    clock = FakeClock(_CLOCK_NOW)
    ming = make_student(db_session)
    supervisor = make_staff(db_session, permissions=["pickup:operate", "pickup:override"])
    tomorrow = make_pickup_authorization(db_session, ming, service_date=_TODAY + timedelta(days=1))
    cancelled = make_pickup_authorization(
        db_session, ming, service_date=_TODAY, status="cancelled", code="654321"
    )

    with pytest.raises(AppError) as not_today:
        override_complete(
            db_session,
            tomorrow.id,
            "備註",
            actor=_current_staff(supervisor),
            meta=_META,
            clock=clock,
        )
    with pytest.raises(AppError) as not_active:
        override_complete(
            db_session,
            cancelled.id,
            "備註",
            actor=_current_staff(supervisor),
            meta=_META,
            clock=clock,
        )

    assert (not_today.value.status, not_today.value.code) == (409, "authorization_not_today")
    assert (not_active.value.status, not_active.value.code) == (409, "authorization_not_active")
    assert _fresh(db_session, tomorrow.id).status == "active"
    assert _audit_logs(db_session, "pickup.override_complete", tomorrow.id) == []


# --- BACKEND-555 公開組裝函式 ---


def test_authorization_public_assembler_matches_out_fields(db_session: Session) -> None:
    class_a = make_class(db_session, name="A班")
    ming = make_student(db_session, name="王小明", grade_level=3, class_=class_a)
    person = make_pickup_person(
        db_session,
        ming,
        name="李阿姨",
        phone="0912-000-202",
        photo_path=build_object_path(uuid4(), "jpg"),
    )
    yesterday = _TODAY - timedelta(days=1)
    locked = make_pickup_authorization(
        db_session,
        ming,
        service_date=yesterday,
        code="135790",
        person=person,
        proxy_name="李阿姨",
        proxy_phone="0912-000-202",
        code_attempts=5,
    )
    completed = make_pickup_authorization(
        db_session, ming, service_date=_TODAY, code="246802", status="completed"
    )

    out = authorization_out(locked, _TODAY)

    assert isinstance(out, PickupAuthorizationOut)
    assert out.model_dump() == {
        "id": locked.id,
        "student_id": ming.id,
        "service_date": yesterday,
        "pickup_person_id": person.id,
        "proxy_name": "李阿姨",
        "proxy_phone": "0912-000-202",
        "code_last4": "5790",
        "status": "active",
        "effective_status": "expired",  # active 且早於今天
        "verified_at": None,
        "verification_method": None,
        "created_at": locked.created_at,
    }
    done = authorization_out(completed, _TODAY)
    assert (done.status, done.effective_status, done.verification_method, done.verified_at) == (
        "completed",
        "completed",
        "code",
        completed.verified_at,
    )
    # 今天的 active 不算過期；明天視角下昨天那筆仍 expired
    assert authorization_out(locked, yesterday).effective_status == "active"
    for dumped in (out.model_dump(), done.model_dump()):
        assert "code_hash" not in dumped
        assert "code_attempts" not in dumped

    brief = student_brief_map(db_session, [ming.id])[ming.id]
    staff_out = staff_authorization_out(
        locked, _TODAY, student=brief, photo_url=f"{_URL_PREFIX}x.jpg", verified_by_name=None
    )

    assert isinstance(staff_out, StaffAuthorizationOut)
    assert staff_out.model_dump() == {
        **out.model_dump(),
        "student": {
            "id": ming.id,
            "student_no": ming.student_no,
            "name": "王小明",
            "grade_level": 3,
            "class_id": class_a.id,
            "class_name": "A班",
        },
        "photo_url": f"{_URL_PREFIX}x.jpg",
        "code_attempts": 5,
        "locked": True,
        "verified_by_name": None,
    }
    teacher = make_staff(db_session, display_name="林老師")
    completed.verified_by = teacher.id
    db_session.flush()
    staff_done = staff_authorization_out(
        completed, _TODAY, student=brief, photo_url=None, verified_by_name="林老師"
    )
    assert (staff_done.locked, staff_done.code_attempts, staff_done.verified_by_name) == (
        False,
        0,
        "林老師",
    )
    assert staff_done.photo_url is None


def _pickup_imports(tree: ast.Module) -> tuple[list[str], dict[str, list[str]]]:
    """回傳 (整個模組 import 的 pickup 模組清單, 各 from-import 的 pickup 模組 → 名稱)。"""
    plain: list[str] = []
    from_imports: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            plain.extend(
                alias.name for alias in node.names if alias.name.startswith("app.services.pickup")
            )
        elif isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
            "app.services.pickup"
        ):
            from_imports.setdefault(node.module or "", []).extend(a.name for a in node.names)
    return plain, from_imports


def test_authorization_public_assembler_is_used_by_close_out(
    db_session: Session, published: list[Call]
) -> None:
    source_file = inspect.getsourcefile(student_service)
    assert source_file is not None
    tree = ast.parse(Path(source_file).read_text(encoding="utf-8"), filename=source_file)
    plain, from_imports = _pickup_imports(tree)

    # 不再以任何形式拿到 pickup 模組的私有名稱：沒有 import 整個模組（避免 module._x），
    # from-import 的名稱都不以底線開頭，且授權輸出改用公開的 authorization_out
    assert plain == []
    private = sorted(
        f"{module}.{name}"
        for module, names in from_imports.items()
        for name in names
        if name.startswith("_")
    )
    assert private == []
    assert "authorization_out" in from_imports.get("app.services.pickup.authorizations", [])

    clock = FakeClock(_CLOCK_NOW)
    ming = make_student(db_session)
    ming.status = "withdrawn"
    ming.withdrawn_on = _TODAY
    db_session.flush()
    staff = make_staff(db_session, permissions=["students:write"])
    auth = make_pickup_authorization(db_session, ming, service_date=_TODAY + timedelta(days=1))

    result = student_service.close_out_inactive_student(
        db_session, ming, actor=_current_staff(staff), clock=clock
    )

    assert result.cancelled_authorizations == 1
    db_session.commit()
    [message] = [
        m
        for ch, m in published
        if ch == [admin_topic_channel("pickup")] and m["type"] == "pickup.authorization_updated"
    ]
    row = _fresh(db_session, auth.id)
    assert row.status == "cancelled"
    assert message["data"] == jsonable_encoder(authorization_out(row, _TODAY).model_dump())
    assert message["data"]["effective_status"] == "cancelled"
