"""DB-010：schools 表（名稱不分大小寫、去頭尾空白唯一，short_name 不可空白，RLS、updated_at）。"""

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
from tests.integration.db.factories import make_schools


def test_schools_name_unique(backend_conn: Conn) -> None:
    for first, second in (("新生國小", " 新生國小"), ("Xinsheng", "xinsheng")):
        make_schools(backend_conn, name=first)
        with pg_error(backend_conn, UNIQUE_VIOLATION) as err:
            make_schools(backend_conn, name=second)
        assert err.constraint_name == "uq_schools_name"


def test_schools_name_not_blank(backend_conn: Conn) -> None:
    for name in ("  ", "校" * 51):
        with pg_error(backend_conn, CHECK_VIOLATION):
            make_schools(backend_conn, name=name)

    assert make_schools(backend_conn, name="校" * 50)["name"] == "校" * 50


def test_schools_short_name_not_blank(backend_conn: Conn) -> None:
    for short_name in ("  ", "新" * 21):
        with pg_error(backend_conn, CHECK_VIOLATION):
            make_schools(backend_conn, short_name=short_name)

    assert make_schools(backend_conn, name="新生國小", short_name=None)["short_name"] is None
    assert make_schools(backend_conn, name="仁愛國小", short_name="仁愛")["short_name"] == "仁愛"


def test_schools_defaults(backend_conn: Conn) -> None:
    row = make_schools(backend_conn, name="新生國小")

    assert row["is_active"] is True
    assert row["short_name"] is None


def test_schools_grants(owner_conn: Conn, backend_conn: Conn) -> None:
    assert_backend_grants(owner_conn, "public.schools")

    row = make_schools(backend_conn)
    assert_backend_read_write(backend_conn, "public.schools", row, short_name="新生")


def test_schools_updated_at_trigger(backend_conn: Conn) -> None:
    row = make_schools(backend_conn, updated_at=SEEDED_UPDATED_AT)

    assert_updated_at_trigger(backend_conn, "public.schools", row["id"], short_name="新生")
