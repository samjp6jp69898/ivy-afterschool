"""DB-021：homework_items 表（status 值域、title 長度、subject set null、student restrict）。"""

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
from tests.integration.db.factories import make_homework_items, make_subjects


def test_homework_items_status_domain(backend_conn: Conn) -> None:
    with pg_error(backend_conn, CHECK_VIOLATION):
        make_homework_items(backend_conn, status="finished")

    for status in ("todo", "doing", "correcting", "done"):
        assert make_homework_items(backend_conn, status=status)["status"] == status


def test_homework_items_title_not_blank(backend_conn: Conn) -> None:
    for bad_title in ("  ", "作" * 101):
        with pg_error(backend_conn, CHECK_VIOLATION):
            make_homework_items(backend_conn, title=bad_title)

    assert make_homework_items(backend_conn, title="作" * 100)["title"] == "作" * 100


def test_homework_items_subject_set_null(backend_conn: Conn) -> None:
    subject = make_subjects(backend_conn)
    row = make_homework_items(backend_conn, subject_id=subject["id"])
    assert row["subject_id"] == subject["id"]

    backend_conn.execute("delete from public.subjects where id = %s", (subject["id"],))

    after = backend_conn.execute(
        "select subject_id from public.homework_items where id = %s", (row["id"],)
    ).fetchone()
    assert after == (None,)


def test_homework_items_student_restrict(backend_conn: Conn) -> None:
    row = make_homework_items(backend_conn)

    with pg_error(backend_conn, FK_VIOLATION):
        backend_conn.execute("delete from public.students where id = %s", (row["student_id"],))


def test_homework_items_defaults(backend_conn: Conn) -> None:
    row = make_homework_items(backend_conn)

    assert row["status"] == "todo"
    assert row["sort_order"] == 0
    assert row["subject_id"] is None
    assert row["updated_by"] is None


def test_homework_items_same_title_allowed(backend_conn: Conn) -> None:
    first = make_homework_items(backend_conn, title="國語習作")
    make_homework_items(backend_conn, student_id=first["student_id"], title="國語習作")


def test_homework_items_grants(owner_conn: Conn, backend_conn: Conn) -> None:
    assert_backend_grants(owner_conn, "public.homework_items")

    row = make_homework_items(backend_conn)
    assert_backend_read_write(backend_conn, "public.homework_items", row, status="doing")


def test_homework_items_updated_at_trigger(backend_conn: Conn) -> None:
    row = make_homework_items(backend_conn, updated_at=SEEDED_UPDATED_AT)

    assert_updated_at_trigger(backend_conn, "public.homework_items", row["id"], status="doing")
