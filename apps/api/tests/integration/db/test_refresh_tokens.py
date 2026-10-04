"""DB-005：refresh_tokens 表（token_hash、subject_type、replaced_by、效期、grant、trigger）。"""

from datetime import datetime, timedelta, timezone
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
from tests.integration.db.factories import make_refresh_tokens

TAIPEI = timezone(timedelta(hours=8))


def test_refresh_tokens_token_hash_unique(backend_conn: Conn) -> None:
    make_refresh_tokens(backend_conn, token_hash="a" * 64)

    with pg_error(backend_conn, UNIQUE_VIOLATION) as err:
        make_refresh_tokens(backend_conn, token_hash="a" * 64)
    assert err.constraint_name == "uq_refresh_tokens_token_hash"


def test_refresh_tokens_token_hash_format(backend_conn: Conn) -> None:
    for token_hash in ("abc", "A" * 64, "a" * 63, "a" * 65, "g" * 64):
        with pg_error(backend_conn, CHECK_VIOLATION) as err:
            make_refresh_tokens(backend_conn, token_hash=token_hash)
        assert err.constraint_name == "ck_refresh_tokens_token_hash_format", token_hash

    token_hash = "0123456789abcdef" * 4
    assert make_refresh_tokens(backend_conn, token_hash=token_hash)["token_hash"] == token_hash


def test_refresh_tokens_subject_type_domain(backend_conn: Conn) -> None:
    with pg_error(backend_conn, CHECK_VIOLATION):
        make_refresh_tokens(backend_conn, subject_type="device")

    for subject_type in ("staff", "parent"):
        row = make_refresh_tokens(backend_conn, subject_type=subject_type)
        assert row["subject_type"] == subject_type


def test_refresh_tokens_replaced_by_fk(backend_conn: Conn) -> None:
    with pg_error(backend_conn, FK_VIOLATION):
        make_refresh_tokens(backend_conn, replaced_by=uuid4())

    family_id = uuid4()
    new = make_refresh_tokens(backend_conn, family_id=family_id)
    old = make_refresh_tokens(backend_conn, family_id=family_id, replaced_by=new["id"])
    backend_conn.execute("delete from public.refresh_tokens where id = %s", (new["id"],))

    assert backend_conn.execute(
        "select replaced_by from public.refresh_tokens where id = %s", (old["id"],)
    ).fetchone() == (None,)


def test_refresh_tokens_replaced_by_not_self(backend_conn: Conn) -> None:
    row = make_refresh_tokens(backend_conn)

    with pg_error(backend_conn, CHECK_VIOLATION):
        backend_conn.execute(
            "update public.refresh_tokens set replaced_by = id where id = %s", (row["id"],)
        )


def test_refresh_tokens_expires_after_created(backend_conn: Conn) -> None:
    created_at = datetime(2026, 10, 2, 10, 0, tzinfo=TAIPEI)
    for expires_at in (created_at - timedelta(hours=1), created_at):
        with pg_error(backend_conn, CHECK_VIOLATION):
            make_refresh_tokens(backend_conn, created_at=created_at, expires_at=expires_at)

    row = make_refresh_tokens(
        backend_conn, created_at=created_at, expires_at=created_at + timedelta(days=30)
    )
    assert row["expires_at"] == created_at + timedelta(days=30)


def test_refresh_tokens_defaults(backend_conn: Conn) -> None:
    row = make_refresh_tokens(backend_conn)

    assert (row["revoked_at"], row["replaced_by"]) == (None, None)


def test_refresh_tokens_grants(owner_conn: Conn, backend_conn: Conn) -> None:
    assert_backend_grants(owner_conn, "public.refresh_tokens")

    row = make_refresh_tokens(backend_conn)
    revoked_at = datetime(2026, 10, 2, 9, 0, tzinfo=TAIPEI)
    assert_backend_read_write(backend_conn, "public.refresh_tokens", row, revoked_at=revoked_at)


def test_refresh_tokens_updated_at_trigger(backend_conn: Conn) -> None:
    row = make_refresh_tokens(backend_conn, updated_at=SEEDED_UPDATED_AT)

    assert_updated_at_trigger(
        backend_conn,
        "public.refresh_tokens",
        row["id"],
        revoked_at=datetime(2026, 10, 2, 9, 0, tzinfo=TAIPEI),
    )
