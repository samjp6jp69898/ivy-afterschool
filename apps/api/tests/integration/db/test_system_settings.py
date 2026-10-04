"""DB-007：system_settings 表（key 唯一且格式受限、value 必為物件、updated_by set null、updated_at）。"""

from psycopg.types.json import Jsonb

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
from tests.integration.db.factories import make_staff_users, make_system_settings


def test_system_settings_key_unique(backend_conn: Conn) -> None:
    make_system_settings(backend_conn, key="org.profile")

    with pg_error(backend_conn, UNIQUE_VIOLATION) as err:
        make_system_settings(backend_conn, key="org.profile")
    assert err.constraint_name == "uq_system_settings_key"


def test_system_settings_key_format(backend_conn: Conn) -> None:
    for bad_key in ("OrgProfile", "org"):
        with pg_error(backend_conn, CHECK_VIOLATION):
            make_system_settings(backend_conn, key=bad_key)

    assert make_system_settings(backend_conn, key="pickup.window")["key"] == "pickup.window"


def test_system_settings_value_must_be_object(backend_conn: Conn) -> None:
    for bad_value in ([1, 2], "x"):
        with pg_error(backend_conn, CHECK_VIOLATION):
            make_system_settings(backend_conn, value=Jsonb(bad_value))

    assert make_system_settings(backend_conn, value=Jsonb({}))["value"] == {}


def test_system_settings_updated_by_set_null(backend_conn: Conn) -> None:
    staff = make_staff_users(backend_conn)
    row = make_system_settings(backend_conn, updated_by=staff["id"])
    assert row["updated_by"] == staff["id"]

    backend_conn.execute("delete from public.staff_users where id = %s", (staff["id"],))

    after = backend_conn.execute(
        "select updated_by from public.system_settings where id = %s", (row["id"],)
    ).fetchone()
    assert after == (None,)


def test_system_settings_grants(owner_conn: Conn, backend_conn: Conn) -> None:
    assert_backend_grants(owner_conn, "public.system_settings")

    row = make_system_settings(backend_conn)
    assert_backend_read_write(backend_conn, "public.system_settings", row, is_secret=True)


def test_system_settings_updated_at_trigger(backend_conn: Conn) -> None:
    row = make_system_settings(backend_conn, updated_at=SEEDED_UPDATED_AT)

    assert_updated_at_trigger(
        backend_conn, "public.system_settings", row["id"], value=Jsonb({"enabled": False})
    )
