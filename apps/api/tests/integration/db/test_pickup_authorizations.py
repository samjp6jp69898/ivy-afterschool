"""DB-024：pickup_authorizations 表（值域、核銷一致性、碼格式、驗碼鎖定、FK 行為）。"""

from datetime import UTC, datetime

from tests.integration.db.conftest import (
    CHECK_VIOLATION,
    SEEDED_UPDATED_AT,
    Conn,
    assert_backend_grants,
    assert_backend_read_write,
    assert_updated_at_trigger,
    pg_error,
)
from tests.integration.db.factories import make_pickup_authorizations, make_pickup_persons

NOW = datetime(2026, 10, 5, 18, 0, tzinfo=UTC)


def test_pickup_authorizations_status_domain(backend_conn: Conn) -> None:
    with pg_error(backend_conn, CHECK_VIOLATION):
        make_pickup_authorizations(backend_conn, status="expired")
    with pg_error(backend_conn, CHECK_VIOLATION):
        make_pickup_authorizations(backend_conn, verification_method="face")

    assert make_pickup_authorizations(backend_conn)["status"] == "active"


def test_pickup_authorizations_completed_consistency(backend_conn: Conn) -> None:
    bad_rows = (
        {"status": "completed", "verified_at": None},
        {"status": "active", "verified_at": NOW, "verification_method": "code"},
        {"status": "completed", "verified_at": NOW, "verification_method": None},
        {"status": "cancelled", "verified_at": None, "verification_method": "code"},
    )
    for bad in bad_rows:
        with pg_error(backend_conn, CHECK_VIOLATION):
            make_pickup_authorizations(backend_conn, **bad)

    row = make_pickup_authorizations(
        backend_conn, status="completed", verified_at=NOW, verification_method="visual_match"
    )
    assert row["verification_method"] == "visual_match"
    assert make_pickup_authorizations(backend_conn, status="cancelled")["status"] == "cancelled"


def test_pickup_authorizations_code_formats(backend_conn: Conn) -> None:
    for bad in ({"code_last4": "12a4"}, {"code_last4": "12345"}, {"code_hash": "x"}):
        with pg_error(backend_conn, CHECK_VIOLATION):
            make_pickup_authorizations(backend_conn, **bad)

    assert make_pickup_authorizations(backend_conn, code_last4="0007")["code_last4"] == "0007"


def test_pickup_authorizations_proxy_fields(backend_conn: Conn) -> None:
    for bad in ({"proxy_name": " "}, {"proxy_name": "李" * 51}, {"proxy_phone": "abc"}):
        with pg_error(backend_conn, CHECK_VIOLATION):
            make_pickup_authorizations(backend_conn, **bad)


def test_pickup_authorizations_attempts_range(backend_conn: Conn) -> None:
    for bad_attempts in (6, -1):
        with pg_error(backend_conn, CHECK_VIOLATION):
            make_pickup_authorizations(backend_conn, code_attempts=bad_attempts)

    row = make_pickup_authorizations(backend_conn)
    assert row["code_attempts"] == 0
    assert row["code_locked_at"] is None


def test_pickup_authorizations_lock_consistency(backend_conn: Conn) -> None:
    for bad in (
        {"code_attempts": 5, "code_locked_at": None},
        {"code_attempts": 4, "code_locked_at": NOW},
    ):
        with pg_error(backend_conn, CHECK_VIOLATION) as err:
            make_pickup_authorizations(backend_conn, **bad)
        assert err.constraint_name == "ck_pickup_authorizations_lock"

    row = make_pickup_authorizations(backend_conn, code_attempts=5, code_locked_at=NOW)
    assert row["code_locked_at"] == NOW


def test_pickup_authorizations_person_set_null(backend_conn: Conn) -> None:
    person = make_pickup_persons(backend_conn)
    row = make_pickup_authorizations(
        backend_conn,
        student_id=person["student_id"],
        pickup_person_id=person["id"],
        proxy_name="李阿姨",
    )

    backend_conn.execute("delete from public.pickup_persons where id = %s", (person["id"],))

    after = backend_conn.execute(
        "select pickup_person_id, proxy_name from public.pickup_authorizations where id = %s",
        (row["id"],),
    ).fetchone()
    assert after == (None, "李阿姨")


def test_pickup_authorizations_grants(owner_conn: Conn, backend_conn: Conn) -> None:
    assert_backend_grants(owner_conn, "public.pickup_authorizations")

    row = make_pickup_authorizations(backend_conn)
    assert_backend_read_write(
        backend_conn, "public.pickup_authorizations", row, proxy_name="王叔叔"
    )


def test_pickup_authorizations_updated_at_trigger(backend_conn: Conn) -> None:
    row = make_pickup_authorizations(backend_conn, updated_at=SEEDED_UPDATED_AT)

    assert_updated_at_trigger(
        backend_conn, "public.pickup_authorizations", row["id"], proxy_name="王叔叔"
    )
