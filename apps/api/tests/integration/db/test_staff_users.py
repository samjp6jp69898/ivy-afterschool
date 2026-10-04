"""DB-004：staff_users 表（username、argon2id、role FK、萬用碼、預設值、grant、trigger）。"""

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
from tests.integration.db.factories import ARGON2ID_HASH, make_roles, make_staff_users


def test_staff_users_username_unique(backend_conn: Conn) -> None:
    make_staff_users(backend_conn, username="teacher.lin")

    with pg_error(backend_conn, UNIQUE_VIOLATION) as err:
        make_staff_users(backend_conn, username="teacher.lin")
    assert err.constraint_name == "uq_staff_users_username"


def test_staff_users_username_must_be_lowercase(backend_conn: Conn) -> None:
    for username in ("Teacher", "ab", "teacher lin", ".teacher", "a" * 33):
        with pg_error(backend_conn, CHECK_VIOLATION) as err:
            make_staff_users(backend_conn, username=username)
        assert err.constraint_name == "ck_staff_users_username_format", username

    for username in ("abc", "teacher_lin-2.a", "a" * 32):
        assert make_staff_users(backend_conn, username=username)["username"] == username


def test_staff_users_password_hash_must_be_argon2id(backend_conn: Conn) -> None:
    for password_hash in ("plaintext123", "$argon2i$v=19$m=65536,t=3,p=4$abc$def", "$2b$12$abc"):
        with pg_error(backend_conn, CHECK_VIOLATION) as err:
            make_staff_users(backend_conn, password_hash=password_hash)
        assert err.constraint_name == "ck_staff_users_password_hash_argon2id", password_hash

    row = make_staff_users(backend_conn, password_hash=ARGON2ID_HASH)
    assert row["password_hash"] == ARGON2ID_HASH


def test_staff_users_role_fk(backend_conn: Conn) -> None:
    with pg_error(backend_conn, FK_VIOLATION):
        make_staff_users(backend_conn, role_id=uuid4())

    role = make_roles(backend_conn, code="counselor")
    make_staff_users(backend_conn, role_id=role["id"])
    with pg_error(backend_conn, FK_VIOLATION):
        backend_conn.execute("delete from public.roles where id = %s", (role["id"],))

    assert backend_conn.execute(
        "select count(*) from public.roles where id = %s", (role["id"],)
    ).fetchone() == (1,)


def test_staff_users_no_wildcard_extra(backend_conn: Conn) -> None:
    with pg_error(backend_conn, CHECK_VIOLATION) as err:
        make_staff_users(backend_conn, extra_permissions=["*"])
    assert err.constraint_name == "ck_staff_users_no_wildcard"

    row = make_staff_users(backend_conn, extra_permissions=["students:read"])
    with pg_error(backend_conn, CHECK_VIOLATION) as err:
        backend_conn.execute(
            "update public.staff_users set extra_permissions = '{students:read,*}' where id = %s",
            (row["id"],),
        )
    assert err.constraint_name == "ck_staff_users_no_wildcard"

    # 收回萬用碼不受限（revoked 只會減權限）
    row = make_staff_users(backend_conn, revoked_permissions=["*"])
    assert row["revoked_permissions"] == ["*"]


def test_staff_users_display_name_and_email_format(backend_conn: Conn) -> None:
    for display_name in ("   ", "林" * 51):
        with pg_error(backend_conn, CHECK_VIOLATION):
            make_staff_users(backend_conn, display_name=display_name)
    for email in ("teacher", "a b@example.com", "a@b@c"):
        with pg_error(backend_conn, CHECK_VIOLATION):
            make_staff_users(backend_conn, email=email)

    row = make_staff_users(backend_conn, display_name="林" * 50, email="lin@example.com")
    assert (row["display_name"], row["email"]) == ("林" * 50, "lin@example.com")


def test_staff_users_defaults(backend_conn: Conn) -> None:
    row = make_staff_users(backend_conn)

    assert row["is_active"] is True
    assert row["token_version"] == 0
    assert row["must_change_password"] is True
    assert row["extra_permissions"] == []
    assert row["revoked_permissions"] == []
    assert row["last_login_at"] is None
    assert (row["phone"], row["email"]) == (None, None)


def test_staff_users_token_version_non_negative(backend_conn: Conn) -> None:
    with pg_error(backend_conn, CHECK_VIOLATION):
        make_staff_users(backend_conn, token_version=-1)


def test_staff_users_grants(owner_conn: Conn, backend_conn: Conn) -> None:
    assert_backend_grants(owner_conn, "public.staff_users")

    row = make_staff_users(backend_conn)
    assert_backend_read_write(backend_conn, "public.staff_users", row, display_name="王老師")


def test_staff_users_updated_at_trigger(backend_conn: Conn) -> None:
    row = make_staff_users(backend_conn, updated_at=SEEDED_UPDATED_AT)

    assert_updated_at_trigger(backend_conn, "public.staff_users", row["id"], is_active=False)
