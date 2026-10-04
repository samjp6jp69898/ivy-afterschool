"""DB-027：exam_subjects 表（考試科目唯一、滿分範圍與預設值、FK 行為、updated_at）。"""

from decimal import Decimal

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
from tests.integration.db.factories import make_exam_subjects


def test_exam_subjects_unique_pair(backend_conn: Conn) -> None:
    first = make_exam_subjects(backend_conn)

    with pg_error(backend_conn, UNIQUE_VIOLATION) as err:
        make_exam_subjects(backend_conn, exam_id=first["exam_id"], subject_id=first["subject_id"])
    assert err.constraint_name == "uq_exam_subjects_exam_subject"


def test_exam_subjects_full_score_range(backend_conn: Conn) -> None:
    for bad_score in (Decimal("0"), Decimal("-5"), Decimal("1000.01")):
        with pg_error(backend_conn, CHECK_VIOLATION):
            make_exam_subjects(backend_conn, full_score=bad_score)

    assert make_exam_subjects(backend_conn)["full_score"] == Decimal("100.00")
    assert make_exam_subjects(backend_conn, full_score=Decimal("1000"))["full_score"] == 1000


def test_exam_subjects_fk(backend_conn: Conn) -> None:
    by_exam = make_exam_subjects(backend_conn)
    backend_conn.execute("delete from public.exams where id = %s", (by_exam["exam_id"],))
    remaining = backend_conn.execute(
        "select count(*) from public.exam_subjects where id = %s", (by_exam["id"],)
    ).fetchone()
    assert remaining == (0,)

    by_subject = make_exam_subjects(backend_conn)
    with pg_error(backend_conn, FK_VIOLATION):
        backend_conn.execute(
            "delete from public.subjects where id = %s", (by_subject["subject_id"],)
        )


def test_exam_subjects_grants(owner_conn: Conn, backend_conn: Conn) -> None:
    assert_backend_grants(owner_conn, "public.exam_subjects")

    row = make_exam_subjects(backend_conn)
    assert_backend_read_write(backend_conn, "public.exam_subjects", row, sort_order=3)


def test_exam_subjects_updated_at_trigger(backend_conn: Conn) -> None:
    row = make_exam_subjects(backend_conn, updated_at=SEEDED_UPDATED_AT)

    assert_updated_at_trigger(backend_conn, "public.exam_subjects", row["id"], sort_order=3)
