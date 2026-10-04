"""BACKEND-401：app/models/pickup.py（接送人、代理授權、接送請求）與接送 factory。"""

from collections.abc import Iterator
from datetime import UTC, date, datetime, time
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.crypto import derive_key
from app.models.pickup import (
    OPEN_STATUSES,
    TERMINAL_STATUSES,
    UQ_ONE_OPEN,
    PickupAuthorization,
    PickupPerson,
    PickupRequest,
)
from app.services.pickup.codes import hash_pickup_code
from tests.integration.test_schema_drift import PENDING_MODEL_TABLES
from tests.support.factories import (
    make_pickup_authorization,
    make_pickup_person,
    make_pickup_request,
    make_student,
)

_DAY = date(2026, 9, 1)


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


def _constraint(exc: IntegrityError) -> str | None:
    diag = getattr(exc.orig, "diag", None)
    return None if diag is None else diag.constraint_name


def test_pickup_models_roundtrip(db_session: Session) -> None:
    student = make_student(db_session)
    person = make_pickup_person(db_session, student)
    request = make_pickup_request(db_session, student, service_date=_DAY)
    auth = make_pickup_authorization(db_session, student, service_date=_DAY, person=person)
    db_session.expire_all()

    loaded_request = db_session.execute(
        select(PickupRequest).where(PickupRequest.id == request.id)
    ).scalar_one()
    assert loaded_request.status == "pending"
    assert loaded_request.source == "parent"
    assert loaded_request.requested_by_type == "parent"
    assert loaded_request.reply_source is None
    assert loaded_request.arrived_at is None

    loaded_auth = db_session.execute(
        select(PickupAuthorization).where(PickupAuthorization.id == auth.id)
    ).scalar_one()
    assert loaded_auth.code_last4 == "3456"
    assert loaded_auth.code_attempts == 0
    assert loaded_auth.code_locked_at is None
    assert loaded_auth.status == "active"
    assert loaded_auth.code_hash == hash_pickup_code("123456")
    assert loaded_auth.pickup_person is not None
    assert loaded_auth.pickup_person.name == "李阿姨"

    loaded_person = db_session.execute(
        select(PickupPerson).where(PickupPerson.id == person.id)
    ).scalar_one()
    assert (loaded_person.relation, loaded_person.phone) == ("阿姨", "0912-000-101")
    assert loaded_person.archived_at is None


def test_pickup_models_one_open(db_session: Session) -> None:
    student = make_student(db_session)
    first = make_pickup_request(db_session, student, service_date=_DAY)

    with pytest.raises(IntegrityError) as excinfo, db_session.begin_nested():
        make_pickup_request(db_session, student, service_date=_DAY, status="acknowledged")

    assert getattr(excinfo.value.orig, "sqlstate", None) == "23505"
    assert _constraint(excinfo.value) == UQ_ONE_OPEN == "uq_pickup_requests_one_open"

    first.status = "completed"
    first.completed_at = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
    first.completion_method = "override"
    db_session.flush()
    second = make_pickup_request(db_session, student, service_date=_DAY)
    assert second.status == "pending"
    # 另一天不受影響
    make_pickup_request(db_session, student, service_date=date(2026, 9, 2))


def test_pickup_models_status_sets() -> None:
    assert frozenset({"pending", "acknowledged", "arrived"}) == OPEN_STATUSES
    assert frozenset({"completed", "cancelled", "expired"}) == TERMINAL_STATUSES
    assert not OPEN_STATUSES & TERMINAL_STATUSES


@pytest.mark.parametrize(
    ("status", "attr"),
    [
        ("arrived", "arrived_at"),
        ("completed", "completed_at"),
        ("cancelled", "cancelled_at"),
    ],
)
def test_pickup_models_request_factory_fills_status_columns(
    db_session: Session, status: str, attr: str
) -> None:
    request = make_pickup_request(
        db_session,
        make_student(db_session),
        service_date=_DAY,
        status=status,  # type: ignore[arg-type]
    )

    assert getattr(request, attr) is not None
    if status == "completed":
        assert request.completion_method == "override"


def test_pickup_models_request_factory_reply_columns(db_session: Session) -> None:
    staff_reply = make_pickup_request(
        db_session,
        make_student(db_session),
        service_date=_DAY,
        reply_source="staff",
        reply_message="已通知",
        reply_ready_eta=time(17, 30),
    )
    auto_reply = make_pickup_request(
        db_session, make_student(db_session), service_date=_DAY, reply_source="auto"
    )

    assert staff_reply.replied_by is not None
    assert staff_reply.replied_at is not None
    assert staff_reply.reply_ready_eta == time(17, 30)
    assert auto_reply.replied_by is None
    assert auto_reply.replied_at is not None


def test_pickup_models_lock_check(db_session: Session) -> None:
    student = make_student(db_session)
    db_session.add(
        PickupAuthorization(
            student_id=student.id,
            service_date=_DAY,
            proxy_name="李阿姨",
            proxy_phone="0912-000-101",
            code_hash=hash_pickup_code("123456"),
            code_last4="3456",
            code_attempts=5,
            code_locked_at=None,
        )
    )

    with pytest.raises(IntegrityError) as excinfo, db_session.begin_nested():
        db_session.flush()

    assert _constraint(excinfo.value) == "ck_pickup_authorizations_lock"


def test_pickup_models_lock_factory_fills_locked_at(db_session: Session) -> None:
    auth = make_pickup_authorization(
        db_session, make_student(db_session), service_date=_DAY, code_attempts=5
    )

    assert auth.code_attempts == 5
    assert auth.code_locked_at is not None


def test_pickup_models_authorization_without_person(db_session: Session) -> None:
    auth = make_pickup_authorization(
        db_session,
        make_student(db_session),
        service_date=_DAY,
        code="654321",
        proxy_name="張伯伯",
        proxy_phone="0912-000-202",
    )

    assert auth.pickup_person_id is None
    assert auth.pickup_person is None
    assert (auth.proxy_name, auth.proxy_phone, auth.code_last4) == (
        "張伯伯",
        "0912-000-202",
        "4321",
    )
    assert auth.created_by_parent_id is None
    assert uuid4() != auth.id


def test_pickup_models_not_pending() -> None:
    for table in ("pickup_persons", "pickup_authorizations", "pickup_requests"):
        assert table not in PENDING_MODEL_TABLES
