"""DB-009：exam_types 表（名稱不分大小寫、去頭尾空白唯一，RLS、updated_at）。"""

from tests.integration.db.conftest import (
    CHECK_VIOLATION,
    SEEDED_UPDATED_AT,
    UNIQUE_VIOLATION,
    Conn,
    assert_backend_read_write,
    assert_table_secured,
    assert_updated_at_trigger,
    pg_error,
)
from tests.integration.db.factories import make_exam_types


def test_exam_types_name_unique_case_insensitive(backend_conn: Conn) -> None:
    for first, second in (("週考", "週考 "), ("Quiz", "quiz")):
        make_exam_types(backend_conn, name=first)
        with pg_error(backend_conn, UNIQUE_VIOLATION) as err:
            make_exam_types(backend_conn, name=second)
        assert err.constraint_name == "uq_exam_types_name"


def test_exam_types_name_not_blank(backend_conn: Conn) -> None:
    for name in ("", "考" * 21):
        with pg_error(backend_conn, CHECK_VIOLATION):
            make_exam_types(backend_conn, name=name)

    assert make_exam_types(backend_conn, name="考" * 20)["name"] == "考" * 20


def test_exam_types_defaults(backend_conn: Conn) -> None:
    row = make_exam_types(backend_conn, name="週考")

    assert row["sort_order"] == 0
    assert row["is_active"] is True


def test_exam_types_secured(owner_conn: Conn, backend_conn: Conn) -> None:
    assert_table_secured(owner_conn, "public.exam_types")

    row = make_exam_types(backend_conn)
    assert_backend_read_write(backend_conn, "public.exam_types", row, sort_order=5)


def test_exam_types_updated_at_trigger(backend_conn: Conn) -> None:
    row = make_exam_types(backend_conn, updated_at=SEEDED_UPDATED_AT)

    assert_updated_at_trigger(backend_conn, "public.exam_types", row["id"], sort_order=5)
