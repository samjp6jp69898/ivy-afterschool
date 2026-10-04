"""DB-026：exams 表（應考範圍、grade_level 範圍、發布一致性、FK restrict、updated_at）。"""

from datetime import UTC, datetime

from tests.integration.db.conftest import (
    CHECK_VIOLATION,
    FK_VIOLATION,
    SEEDED_UPDATED_AT,
    Conn,
    assert_backend_grants,
    assert_backend_read_write,
    assert_updated_at_trigger,
    pg_error,
)
from tests.integration.db.factories import make_classes, make_exams

PUBLISHED_AT = datetime(2026, 10, 21, 9, 0, tzinfo=UTC)


def test_exams_scope_required(backend_conn: Conn) -> None:
    with pg_error(backend_conn, CHECK_VIOLATION) as err:
        make_exams(backend_conn, grade_level=None, class_id=None)
    assert err.constraint_name == "ck_exams_scope"

    assert make_exams(backend_conn, grade_level=3)["grade_level"] == 3
    classroom = make_classes(backend_conn)
    assert make_exams(backend_conn, class_id=classroom["id"])["class_id"] == classroom["id"]


def test_exams_grade_level_range(backend_conn: Conn) -> None:
    for bad_level in (0, 7):
        with pg_error(backend_conn, CHECK_VIOLATION):
            make_exams(backend_conn, grade_level=bad_level)


def test_exams_status_published_consistency(backend_conn: Conn) -> None:
    for bad in (
        {"status": "published", "published_at": None},
        {"status": "draft", "published_at": PUBLISHED_AT},
        {"status": "archived"},
    ):
        with pg_error(backend_conn, CHECK_VIOLATION):
            make_exams(backend_conn, **bad)

    assert make_exams(backend_conn)["status"] == "draft"
    published = make_exams(backend_conn, status="published", published_at=PUBLISHED_AT)
    assert published["published_at"] == PUBLISHED_AT


def test_exams_fk_restrict(backend_conn: Conn) -> None:
    classroom = make_classes(backend_conn)
    row = make_exams(backend_conn, class_id=classroom["id"])

    with pg_error(backend_conn, FK_VIOLATION):
        backend_conn.execute("delete from public.exam_types where id = %s", (row["exam_type_id"],))
    with pg_error(backend_conn, FK_VIOLATION):
        backend_conn.execute("delete from public.classes where id = %s", (classroom["id"],))


def test_exams_name_and_note_length(backend_conn: Conn) -> None:
    for bad in ({"name": " "}, {"name": "考" * 51}, {"note": "註" * 501}):
        with pg_error(backend_conn, CHECK_VIOLATION):
            make_exams(backend_conn, **bad)


def test_exams_grants(owner_conn: Conn, backend_conn: Conn) -> None:
    assert_backend_grants(owner_conn, "public.exams")

    row = make_exams(backend_conn)
    assert_backend_read_write(backend_conn, "public.exams", row, note="範圍：第一至三單元")


def test_exams_updated_at_trigger(backend_conn: Conn) -> None:
    row = make_exams(backend_conn, updated_at=SEEDED_UPDATED_AT)

    assert_updated_at_trigger(backend_conn, "public.exams", row["id"], note="範圍：第一至三單元")
