"""DB-013：class_staff 表（同員工同班唯一、role 值域、班級 / 員工刪除連帶刪除、updated_at）。"""

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
from tests.integration.db.factories import make_class_staff, make_classes, make_staff_users


def _count(conn: Conn, row_id: object) -> int:
    row = conn.execute(
        "select count(*) from public.class_staff where id = %s", (row_id,)
    ).fetchone()
    assert row is not None
    return row[0]


def test_class_staff_unique_pair(backend_conn: Conn) -> None:
    row = make_class_staff(backend_conn)

    with pg_error(backend_conn, UNIQUE_VIOLATION) as err:
        make_class_staff(backend_conn, class_id=row["class_id"], staff_user_id=row["staff_user_id"])
    assert err.constraint_name == "uq_class_staff_class_staff"


def test_class_staff_default_role_and_domain(backend_conn: Conn) -> None:
    assert make_class_staff(backend_conn)["role"] == "assistant"

    with pg_error(backend_conn, CHECK_VIOLATION):
        make_class_staff(backend_conn, role="teacher")


def test_class_staff_multiple_leads_allowed(backend_conn: Conn) -> None:
    classroom = make_classes(backend_conn)
    for _ in range(2):
        make_class_staff(backend_conn, class_id=classroom["id"], role="lead")


def test_class_staff_fk(backend_conn: Conn) -> None:
    with pg_error(backend_conn, FK_VIOLATION):
        make_class_staff(backend_conn, class_id=uuid4())

    by_class = make_class_staff(backend_conn)
    backend_conn.execute("delete from public.classes where id = %s", (by_class["class_id"],))
    assert _count(backend_conn, by_class["id"]) == 0

    by_staff = make_class_staff(backend_conn)
    backend_conn.execute(
        "delete from public.staff_users where id = %s", (by_staff["staff_user_id"],)
    )
    assert _count(backend_conn, by_staff["id"]) == 0


def test_class_staff_grants(owner_conn: Conn, backend_conn: Conn) -> None:
    assert_backend_grants(owner_conn, "public.class_staff")

    row = make_class_staff(backend_conn)
    assert_backend_read_write(backend_conn, "public.class_staff", row, role="lead")


def test_class_staff_updated_at_trigger(backend_conn: Conn) -> None:
    row = make_class_staff(backend_conn, updated_at=SEEDED_UPDATED_AT)

    assert_updated_at_trigger(backend_conn, "public.class_staff", row["id"], role="lead")
