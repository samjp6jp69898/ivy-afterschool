"""DB-008：subjects 表（名稱不分大小寫、去頭尾空白唯一，RLS、updated_at）。"""

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
from tests.integration.db.factories import make_subjects


def test_subjects_name_unique_case_insensitive(backend_conn: Conn) -> None:
    for first, second in (("書法", " 書法 "), ("Art", "art")):
        make_subjects(backend_conn, name=first)
        with pg_error(backend_conn, UNIQUE_VIOLATION) as err:
            make_subjects(backend_conn, name=second)
        assert err.constraint_name == "uq_subjects_name"


def test_subjects_name_not_blank(backend_conn: Conn) -> None:
    for name in ("   ", "書" * 21):
        with pg_error(backend_conn, CHECK_VIOLATION):
            make_subjects(backend_conn, name=name)

    assert make_subjects(backend_conn, name="書" * 20)["name"] == "書" * 20


def test_subjects_defaults(backend_conn: Conn) -> None:
    row = make_subjects(backend_conn, name="書法")

    assert row["sort_order"] == 0
    assert row["is_active"] is True


def test_subjects_grants(owner_conn: Conn, backend_conn: Conn) -> None:
    assert_backend_grants(owner_conn, "public.subjects")

    row = make_subjects(backend_conn)
    assert_backend_read_write(backend_conn, "public.subjects", row, sort_order=5)


def test_subjects_updated_at_trigger(backend_conn: Conn) -> None:
    row = make_subjects(backend_conn, updated_at=SEEDED_UPDATED_AT)

    assert_updated_at_trigger(backend_conn, "public.subjects", row["id"], sort_order=5)
