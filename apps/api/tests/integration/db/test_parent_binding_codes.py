"""DB-017：parent_binding_codes 表（code_hash 唯一與格式、有效期、FK 行為、updated_at）。"""

from datetime import UTC, datetime

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
from tests.integration.db.factories import make_parent_binding_codes

CODE_HASH = "a" * 64


def test_parent_binding_codes_code_hash_unique(backend_conn: Conn) -> None:
    make_parent_binding_codes(backend_conn, code_hash=CODE_HASH)

    with pg_error(backend_conn, UNIQUE_VIOLATION) as err:
        make_parent_binding_codes(backend_conn, code_hash=CODE_HASH)
    assert err.constraint_name == "uq_parent_binding_codes_code_hash"


def test_parent_binding_codes_code_hash_format(backend_conn: Conn) -> None:
    for bad_hash in ("ABCD1234", "A" * 64, "a" * 63, "g" * 64):
        with pg_error(backend_conn, CHECK_VIOLATION):
            make_parent_binding_codes(backend_conn, code_hash=bad_hash)

    assert make_parent_binding_codes(backend_conn, code_hash=CODE_HASH)["code_hash"] == CODE_HASH


def test_parent_binding_codes_expires_after_created(backend_conn: Conn) -> None:
    created_at = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)
    for expires_at in (datetime(2026, 10, 5, 8, 59, tzinfo=UTC), created_at):
        with pg_error(backend_conn, CHECK_VIOLATION):
            make_parent_binding_codes(backend_conn, created_at=created_at, expires_at=expires_at)

    row = make_parent_binding_codes(
        backend_conn, created_at=created_at, expires_at=datetime(2026, 10, 12, 9, 0, tzinfo=UTC)
    )
    assert row["used_at"] is None


def test_parent_binding_codes_fk(backend_conn: Conn) -> None:
    row = make_parent_binding_codes(backend_conn)

    with pg_error(backend_conn, FK_VIOLATION):
        backend_conn.execute("delete from public.staff_users where id = %s", (row["created_by"],))

    backend_conn.execute("delete from public.guardians where id = %s", (row["guardian_id"],))
    remaining = backend_conn.execute(
        "select count(*) from public.parent_binding_codes where id = %s", (row["id"],)
    ).fetchone()
    assert remaining == (0,)


def test_parent_binding_codes_grants(owner_conn: Conn, backend_conn: Conn) -> None:
    assert_backend_grants(owner_conn, "public.parent_binding_codes")

    row = make_parent_binding_codes(backend_conn)
    assert_backend_read_write(
        backend_conn, "public.parent_binding_codes", row, used_at=datetime.now(UTC)
    )


def test_parent_binding_codes_updated_at_trigger(backend_conn: Conn) -> None:
    row = make_parent_binding_codes(backend_conn, updated_at=SEEDED_UPDATED_AT)

    assert_updated_at_trigger(
        backend_conn, "public.parent_binding_codes", row["id"], used_at=datetime.now(UTC)
    )
