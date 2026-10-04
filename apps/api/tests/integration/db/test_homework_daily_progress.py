"""DB-022：homework_daily_progress 表（每生每日唯一與 upsert、值域、ETA 稽核、note 長度、updated_at）。"""

from datetime import UTC, datetime, time

from tests.integration.db.conftest import (
    CHECK_VIOLATION,
    SEEDED_UPDATED_AT,
    UNIQUE_VIOLATION,
    Conn,
    assert_backend_grants,
    assert_backend_read_write,
    assert_updated_at_trigger,
    pg_error,
)
from tests.integration.db.factories import make_homework_daily_progress

ETA_UPDATED_AT = datetime(2026, 10, 5, 9, 30, tzinfo=UTC)


def test_homework_daily_progress_unique_student_date(backend_conn: Conn) -> None:
    first = make_homework_daily_progress(backend_conn)

    with pg_error(backend_conn, UNIQUE_VIOLATION) as err:
        make_homework_daily_progress(backend_conn, student_id=first["student_id"])
    assert err.constraint_name == "uq_homework_daily_progress_student_date"


def test_homework_daily_progress_upsert_on_conflict(backend_conn: Conn) -> None:
    first = make_homework_daily_progress(backend_conn)

    backend_conn.execute(
        """
        insert into public.homework_daily_progress
            (student_id, service_date, ready_eta, eta_updated_at)
        values (%s, %s, '17:30', now())
        on conflict (student_id, service_date) do update set ready_eta = '17:30'
        """,
        (first["student_id"], first["service_date"]),
    )

    rows = backend_conn.execute(
        "select ready_eta from public.homework_daily_progress where student_id = %s",
        (first["student_id"],),
    ).fetchall()
    assert rows == [(time(17, 30),)]


def test_homework_daily_progress_status_domain(backend_conn: Conn) -> None:
    with pg_error(backend_conn, CHECK_VIOLATION):
        make_homework_daily_progress(backend_conn, overall_status="todo")

    row = make_homework_daily_progress(backend_conn)
    assert row["overall_status"] == "not_started"


def test_homework_daily_progress_eta_requires_timestamp(backend_conn: Conn) -> None:
    with pg_error(backend_conn, CHECK_VIOLATION) as err:
        make_homework_daily_progress(backend_conn, ready_eta=time(17, 30), eta_updated_at=None)
    assert err.constraint_name == "ck_homework_daily_progress_eta_audit"

    row = make_homework_daily_progress(
        backend_conn, ready_eta=time(17, 30), eta_updated_at=ETA_UPDATED_AT
    )
    assert row["ready_eta"] == time(17, 30)


def test_homework_daily_progress_note_length(backend_conn: Conn) -> None:
    with pg_error(backend_conn, CHECK_VIOLATION):
        make_homework_daily_progress(backend_conn, note="註" * 201)

    assert make_homework_daily_progress(backend_conn, note="註" * 200)["note"] == "註" * 200


def test_homework_daily_progress_grants(owner_conn: Conn, backend_conn: Conn) -> None:
    assert_backend_grants(owner_conn, "public.homework_daily_progress")

    row = make_homework_daily_progress(backend_conn)
    assert_backend_read_write(
        backend_conn, "public.homework_daily_progress", row, overall_status="in_progress"
    )


def test_homework_daily_progress_updated_at_trigger(backend_conn: Conn) -> None:
    row = make_homework_daily_progress(backend_conn, updated_at=SEEDED_UPDATED_AT)

    assert_updated_at_trigger(
        backend_conn, "public.homework_daily_progress", row["id"], overall_status="in_progress"
    )
