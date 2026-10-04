"""DB-018：student_leaves 表（值域、日期區間、取消一致性、active 區間禁止重疊、FK、updated_at）。

禁止重疊的 exclusion constraint 依賴 btree_gist（安裝在 extensions schema），一律以 app_backend 寫入驗證。
"""

from datetime import date, datetime
from uuid import uuid4

from tests.integration.db.conftest import (
    CHECK_VIOLATION,
    EXCLUSION_VIOLATION,
    FK_VIOLATION,
    SEEDED_UPDATED_AT,
    Conn,
    assert_backend_grants,
    assert_backend_read_write,
    assert_updated_at_trigger,
    pg_error,
)
from tests.integration.db.factories import make_student_leaves, make_students

CANCELLED_AT = datetime.fromisoformat("2026-10-04T09:00:00+08:00")


def test_student_leaves_enum_domains(backend_conn: Conn) -> None:
    for bad in ({"leave_type": "annual"}, {"status": "approved"}, {"created_by_type": "system"}):
        with pg_error(backend_conn, CHECK_VIOLATION):
            make_student_leaves(backend_conn, **bad)


def test_student_leaves_date_range(backend_conn: Conn) -> None:
    with pg_error(backend_conn, CHECK_VIOLATION) as err:
        make_student_leaves(backend_conn, start_date=date(2026, 10, 5), end_date=date(2026, 10, 4))
    assert err.constraint_name == "ck_student_leaves_range"

    with pg_error(backend_conn, CHECK_VIOLATION) as err:
        make_student_leaves(backend_conn, start_date=date(2026, 10, 1), end_date=date(2026, 12, 31))
    assert err.constraint_name == "ck_student_leaves_range"

    # 單日、以及剛好 61 天（差 60 天）都合法
    make_student_leaves(backend_conn, start_date=date(2026, 10, 5), end_date=date(2026, 10, 5))
    make_student_leaves(backend_conn, start_date=date(2026, 10, 1), end_date=date(2026, 11, 30))


def test_student_leaves_cancel_consistency(backend_conn: Conn) -> None:
    for bad in (
        {"status": "cancelled"},
        {"status": "active", "cancelled_at": CANCELLED_AT},
        {"status": "cancelled", "cancelled_at": CANCELLED_AT, "cancelled_by_type": "staff"},
    ):
        with pg_error(backend_conn, CHECK_VIOLATION):
            make_student_leaves(backend_conn, **bad)

    row = make_student_leaves(
        backend_conn,
        status="cancelled",
        cancelled_at=CANCELLED_AT,
        cancelled_by_type="staff",
        cancelled_by_id=uuid4(),
    )
    assert row["status"] == "cancelled"

    with pg_error(backend_conn, CHECK_VIOLATION):
        make_student_leaves(
            backend_conn,
            status="cancelled",
            cancelled_at=CANCELLED_AT,
            cancelled_by_type="system",
            cancelled_by_id=uuid4(),
        )


def test_student_leaves_student_restrict(backend_conn: Conn) -> None:
    row = make_student_leaves(backend_conn)

    with pg_error(backend_conn, FK_VIOLATION):
        backend_conn.execute("delete from public.students where id = %s", (row["student_id"],))


def test_student_leaves_no_overlap_for_active(backend_conn: Conn) -> None:
    first = make_student_leaves(
        backend_conn, start_date=date(2026, 10, 5), end_date=date(2026, 10, 7)
    )

    # 頭尾相接的同一天也算重疊
    with pg_error(backend_conn, EXCLUSION_VIOLATION) as err:
        make_student_leaves(
            backend_conn,
            student_id=first["student_id"],
            start_date=date(2026, 10, 7),
            end_date=date(2026, 10, 8),
        )
    assert err.constraint_name == "ex_student_leaves_no_overlap"

    make_student_leaves(
        backend_conn,
        student_id=first["student_id"],
        start_date=date(2026, 10, 8),
        end_date=date(2026, 10, 9),
    )
    other_student = make_students(backend_conn)
    make_student_leaves(
        backend_conn,
        student_id=other_student["id"],
        start_date=date(2026, 10, 6),
        end_date=date(2026, 10, 6),
    )


def test_student_leaves_cancelled_not_counted_for_overlap(backend_conn: Conn) -> None:
    first = make_student_leaves(
        backend_conn, start_date=date(2026, 10, 5), end_date=date(2026, 10, 7)
    )
    backend_conn.execute(
        """
        update public.student_leaves
        set status = 'cancelled', cancelled_at = %s, cancelled_by_type = 'staff',
            cancelled_by_id = %s
        where id = %s
        """,
        (CANCELLED_AT, uuid4(), first["id"]),
    )

    again = make_student_leaves(
        backend_conn,
        student_id=first["student_id"],
        start_date=date(2026, 10, 6),
        end_date=date(2026, 10, 6),
    )
    assert again["status"] == "active"


def test_student_leaves_grants(owner_conn: Conn, backend_conn: Conn) -> None:
    assert_backend_grants(owner_conn, "public.student_leaves")

    row = make_student_leaves(backend_conn)
    assert_backend_read_write(backend_conn, "public.student_leaves", row, reason="發燒")


def test_student_leaves_updated_at_trigger(backend_conn: Conn) -> None:
    row = make_student_leaves(backend_conn, updated_at=SEEDED_UPDATED_AT)

    assert_updated_at_trigger(backend_conn, "public.student_leaves", row["id"], reason="發燒")
