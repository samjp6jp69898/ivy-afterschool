"""DB-020：student_attendances 表（每生每日唯一、值域、時間來源成對、狀態需求、請假關聯、FK）。"""

from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

from tests.integration.db.conftest import (
    CHECK_VIOLATION,
    FK_VIOLATION,
    SEEDED_UPDATED_AT,
    UNIQUE_VIOLATION,
    Conn,
    assert_backend_grants,
    assert_backend_read_write,
    assert_updated_at_trigger,
    pg_error,
)
from tests.integration.db.factories import (
    make_student_attendances,
    make_student_leaves,
    make_students,
)

TAIPEI = timezone(timedelta(hours=8))
CHECK_IN = datetime(2026, 10, 5, 16, 0, tzinfo=TAIPEI)
CHECK_OUT = datetime(2026, 10, 5, 18, 30, tzinfo=TAIPEI)


def test_student_attendances_unique_student_date(backend_conn: Conn) -> None:
    first = make_student_attendances(backend_conn)

    with pg_error(backend_conn, UNIQUE_VIOLATION) as err:
        make_student_attendances(backend_conn, student_id=first["student_id"])
    assert err.constraint_name == "uq_student_attendances_student_date"

    next_day = make_student_attendances(
        backend_conn, student_id=first["student_id"], service_date=date(2026, 10, 6)
    )
    assert next_day["status"] == "expected"


def test_student_attendances_status_domain(backend_conn: Conn) -> None:
    with pg_error(backend_conn, CHECK_VIOLATION):
        make_student_attendances(backend_conn, status="late")


def test_student_attendances_source_pairs(backend_conn: Conn) -> None:
    bad_rows = (
        {"check_in_at": CHECK_IN, "check_in_source": None},
        {"check_in_at": None, "check_in_source": "manual"},
        {"check_in_at": CHECK_IN, "check_in_source": "manual", "check_out_source": "pickup"},
        {"check_in_at": CHECK_IN, "check_in_source": "bus"},
        {
            "check_in_at": CHECK_IN,
            "check_in_source": "manual",
            "check_out_at": CHECK_OUT,
            "check_out_source": "bus",
        },
    )
    for bad in bad_rows:
        with pg_error(backend_conn, CHECK_VIOLATION):
            make_student_attendances(backend_conn, **bad)

    row = make_student_attendances(
        backend_conn,
        check_in_at=CHECK_IN,
        check_in_source="nfc",
        check_out_at=CHECK_OUT,
        check_out_source="pickup",
    )
    assert (row["check_in_source"], row["check_out_source"]) == ("nfc", "pickup")


def test_student_attendances_check_out_after_in(backend_conn: Conn) -> None:
    early_out = datetime(2026, 10, 5, 15, 59, tzinfo=TAIPEI)
    with pg_error(backend_conn, CHECK_VIOLATION) as err:
        make_student_attendances(
            backend_conn,
            check_in_at=CHECK_IN,
            check_in_source="manual",
            check_out_at=early_out,
            check_out_source="manual",
        )
    assert err.constraint_name == "ck_student_attendances_check_out_after_in"

    # 只有離班沒有到班也不合法
    with pg_error(backend_conn, CHECK_VIOLATION):
        make_student_attendances(backend_conn, check_out_at=CHECK_OUT, check_out_source="manual")

    # 到班與離班同一時刻合法
    make_student_attendances(
        backend_conn,
        check_in_at=CHECK_IN,
        check_in_source="manual",
        check_out_at=CHECK_IN,
        check_out_source="manual",
    )


def test_student_attendances_status_requires_times(backend_conn: Conn) -> None:
    with pg_error(backend_conn, CHECK_VIOLATION) as err:
        make_student_attendances(backend_conn, status="present")
    assert err.constraint_name == "ck_student_attendances_status_times"

    with pg_error(backend_conn, CHECK_VIOLATION):
        make_student_attendances(
            backend_conn, status="left", check_in_at=CHECK_IN, check_in_source="manual"
        )

    present = make_student_attendances(
        backend_conn, status="present", check_in_at=CHECK_IN, check_in_source="manual"
    )
    assert present["status"] == "present"
    left = make_student_attendances(
        backend_conn,
        status="left",
        check_in_at=CHECK_IN,
        check_in_source="manual",
        check_out_at=CHECK_OUT,
        check_out_source="pickup",
    )
    assert left["status"] == "left"
    # absent 不需要時間
    assert make_student_attendances(backend_conn, status="absent")["status"] == "absent"


def test_student_attendances_leave_link(backend_conn: Conn) -> None:
    leave = make_student_leaves(backend_conn)

    with pg_error(backend_conn, CHECK_VIOLATION) as err:
        make_student_attendances(backend_conn, status="leave", leave_id=None)
    assert err.constraint_name == "ck_student_attendances_leave_link"

    with pg_error(backend_conn, CHECK_VIOLATION):
        make_student_attendances(backend_conn, status="expected", leave_id=leave["id"])

    row = make_student_attendances(
        backend_conn, student_id=leave["student_id"], status="leave", leave_id=leave["id"]
    )
    assert row["leave_id"] == leave["id"]


def test_student_attendances_fk(backend_conn: Conn) -> None:
    with pg_error(backend_conn, FK_VIOLATION):
        make_student_attendances(backend_conn, status="leave", leave_id=uuid4())

    leave = make_student_leaves(backend_conn)
    make_student_attendances(
        backend_conn, student_id=leave["student_id"], status="leave", leave_id=leave["id"]
    )
    with pg_error(backend_conn, FK_VIOLATION):
        backend_conn.execute("delete from public.student_leaves where id = %s", (leave["id"],))

    other = make_student_attendances(backend_conn, student_id=make_students(backend_conn)["id"])
    with pg_error(backend_conn, FK_VIOLATION):
        backend_conn.execute("delete from public.students where id = %s", (other["student_id"],))


def test_student_attendances_grants(owner_conn: Conn, backend_conn: Conn) -> None:
    assert_backend_grants(owner_conn, "public.student_attendances")

    row = make_student_attendances(backend_conn)
    assert_backend_read_write(backend_conn, "public.student_attendances", row, note="家長電話通知")


def test_student_attendances_updated_at_trigger(backend_conn: Conn) -> None:
    row = make_student_attendances(backend_conn, updated_at=SEEDED_UPDATED_AT)

    assert_updated_at_trigger(
        backend_conn, "public.student_attendances", row["id"], note="家長電話通知"
    )
