"""DB-011：closed_days 表（同一日期只能一筆、reason 長度上限，RLS、updated_at）。"""

from datetime import date

from tests.integration.db.conftest import (
    CHECK_VIOLATION,
    NOT_NULL_VIOLATION,
    SEEDED_UPDATED_AT,
    UNIQUE_VIOLATION,
    Conn,
    assert_backend_read_write,
    assert_table_secured,
    assert_updated_at_trigger,
    pg_error,
)
from tests.integration.db.factories import make_closed_days


def test_closed_days_date_unique(backend_conn: Conn) -> None:
    make_closed_days(backend_conn, date=date(2026, 10, 10))

    with pg_error(backend_conn, UNIQUE_VIOLATION) as err:
        make_closed_days(backend_conn, date=date(2026, 10, 10))
    assert err.constraint_name == "uq_closed_days_date"


def test_closed_days_reason_length(backend_conn: Conn) -> None:
    with pg_error(backend_conn, CHECK_VIOLATION):
        make_closed_days(backend_conn, date=date(2026, 10, 10), reason="休" * 101)

    assert make_closed_days(backend_conn, date=date(2026, 10, 10), reason="休" * 100)["reason"] == (
        "休" * 100
    )
    assert make_closed_days(backend_conn, date=date(2026, 10, 11), reason=None)["reason"] is None


def test_closed_days_date_required(backend_conn: Conn) -> None:
    with pg_error(backend_conn, NOT_NULL_VIOLATION) as err:
        make_closed_days(backend_conn, date=None)
    assert err.error is not None
    assert err.error.diag.column_name == "date"


def test_closed_days_secured(owner_conn: Conn, backend_conn: Conn) -> None:
    assert_table_secured(owner_conn, "public.closed_days")

    row = make_closed_days(backend_conn)
    assert_backend_read_write(backend_conn, "public.closed_days", row, reason="國慶日")


def test_closed_days_updated_at_trigger(backend_conn: Conn) -> None:
    row = make_closed_days(backend_conn, reason="颱風假", updated_at=SEEDED_UPDATED_AT)

    assert_updated_at_trigger(backend_conn, "public.closed_days", row["id"], reason="國慶日")
