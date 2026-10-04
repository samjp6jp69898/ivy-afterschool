"""DB-015：parent_accounts 表（line_user_id 唯一與格式、status、token_version、grant、trigger）。"""

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
from tests.integration.db.factories import make_parent_accounts

LINE_USER_ID = "U" + "a" * 32


def test_parent_accounts_line_user_id_unique(backend_conn: Conn) -> None:
    make_parent_accounts(backend_conn, line_user_id=LINE_USER_ID)

    with pg_error(backend_conn, UNIQUE_VIOLATION) as err:
        make_parent_accounts(backend_conn, line_user_id=LINE_USER_ID)
    assert err.constraint_name == "uq_parent_accounts_line_user_id"


def test_parent_accounts_line_user_id_format(backend_conn: Conn) -> None:
    for line_user_id in ("line-123", "U" + "a" * 31, "U" + "A" * 32, "u" + "a" * 32):
        with pg_error(backend_conn, CHECK_VIOLATION):
            make_parent_accounts(backend_conn, line_user_id=line_user_id)

    line_user_id = "U0123456789abcdef0123456789abcdef"
    row = make_parent_accounts(backend_conn, line_user_id=line_user_id)
    assert row["line_user_id"] == line_user_id


def test_parent_accounts_status_domain(backend_conn: Conn) -> None:
    with pg_error(backend_conn, CHECK_VIOLATION):
        make_parent_accounts(backend_conn, status="blocked")

    assert make_parent_accounts(backend_conn, status="disabled")["status"] == "disabled"


def test_parent_accounts_token_version_non_negative(backend_conn: Conn) -> None:
    with pg_error(backend_conn, CHECK_VIOLATION):
        make_parent_accounts(backend_conn, token_version=-1)


def test_parent_accounts_display_name_length(backend_conn: Conn) -> None:
    with pg_error(backend_conn, CHECK_VIOLATION):
        make_parent_accounts(backend_conn, display_name="王" * 101)

    assert make_parent_accounts(backend_conn, display_name=None)["display_name"] is None


def test_parent_accounts_defaults(backend_conn: Conn) -> None:
    row = make_parent_accounts(backend_conn)

    assert (row["status"], row["token_version"], row["last_login_at"]) == ("active", 0, None)


def test_parent_accounts_grants(owner_conn: Conn, backend_conn: Conn) -> None:
    assert_backend_grants(owner_conn, "public.parent_accounts")

    row = make_parent_accounts(backend_conn)
    assert_backend_read_write(backend_conn, "public.parent_accounts", row, phone="0912-000-123")


def test_parent_accounts_updated_at_trigger(backend_conn: Conn) -> None:
    row = make_parent_accounts(backend_conn, updated_at=SEEDED_UPDATED_AT)

    assert_updated_at_trigger(backend_conn, "public.parent_accounts", row["id"], token_version=1)
