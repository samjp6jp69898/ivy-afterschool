"""DB-003：roles 表（code 唯一與格式、萬用碼只限 admin、權限陣列不含 null、RLS、updated_at）。"""

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
from tests.integration.db.factories import make_roles


def test_roles_code_unique(backend_conn: Conn) -> None:
    make_roles(backend_conn, code="counselor")

    with pg_error(backend_conn, UNIQUE_VIOLATION) as err:
        make_roles(backend_conn, code="counselor")
    assert err.constraint_name == "uq_roles_code"


def test_roles_code_format(backend_conn: Conn) -> None:
    for code in ("Director", "a"):
        with pg_error(backend_conn, CHECK_VIOLATION):
            make_roles(backend_conn, code=code)

    assert make_roles(backend_conn, code="front_desk")["code"] == "front_desk"


def test_roles_wildcard_only_for_admin(backend_conn: Conn) -> None:
    with pg_error(backend_conn, CHECK_VIOLATION) as err:
        make_roles(backend_conn, code="front_desk", permissions=["*"])
    assert err.constraint_name == "ck_roles_wildcard_admin_only"

    role = make_roles(backend_conn, code="counselor", permissions=["students:read"])
    with pg_error(backend_conn, CHECK_VIOLATION) as err:
        backend_conn.execute(
            "update public.roles set permissions = '{*}' where id = %s", (role["id"],)
        )
    assert err.constraint_name == "ck_roles_wildcard_admin_only"

    # DB-035 會 seed admin，因此以 upsert 寫入，seed 前後都成立
    admin = backend_conn.execute(
        """
        insert into public.roles (code, name, permissions) values ('admin', '系統管理員', '{*}')
        on conflict (code) do update set permissions = excluded.permissions
        returning permissions
        """
    ).fetchone()
    assert admin == (["*"],)


def test_roles_permissions_reject_null_element(backend_conn: Conn) -> None:
    with pg_error(backend_conn, CHECK_VIOLATION) as err:
        make_roles(backend_conn, permissions=["students:read", None])
    assert err.constraint_name == "ck_roles_permissions_no_null"


def test_roles_name_not_blank(backend_conn: Conn) -> None:
    for name in ("   ", "主" * 51):
        with pg_error(backend_conn, CHECK_VIOLATION):
            make_roles(backend_conn, name=name)

    assert make_roles(backend_conn, name="主" * 50)["name"] == "主" * 50


def test_roles_defaults(backend_conn: Conn) -> None:
    row = make_roles(backend_conn, code="counselor", name="輔導老師")

    assert row["permissions"] == []
    assert row["is_system"] is False
    assert row["description"] is None


def test_roles_secured(owner_conn: Conn, backend_conn: Conn) -> None:
    assert_table_secured(owner_conn, "public.roles")

    row = make_roles(backend_conn)
    assert_backend_read_write(backend_conn, "public.roles", row, name="主任助理")


def test_roles_updated_at_trigger(backend_conn: Conn) -> None:
    row = make_roles(backend_conn, updated_at=SEEDED_UPDATED_AT)

    assert_updated_at_trigger(backend_conn, "public.roles", row["id"], name="課輔組長")
