"""DB-023：pickup_persons 表（name / relation 非空、phone 格式、FK 行為、updated_at）。"""

from tests.integration.db.conftest import (
    CHECK_VIOLATION,
    FK_VIOLATION,
    SEEDED_UPDATED_AT,
    Conn,
    assert_backend_grants,
    assert_backend_read_write,
    assert_updated_at_trigger,
    pg_error,
)
from tests.integration.db.factories import make_parent_accounts, make_pickup_persons


def test_pickup_persons_required_text(backend_conn: Conn) -> None:
    for bad in ({"name": ""}, {"relation": " "}, {"name": "李" * 51}, {"relation": "親" * 21}):
        with pg_error(backend_conn, CHECK_VIOLATION):
            make_pickup_persons(backend_conn, **bad)


def test_pickup_persons_phone_format(backend_conn: Conn) -> None:
    for bad_phone in ("abc", "123", "0" * 21):
        with pg_error(backend_conn, CHECK_VIOLATION):
            make_pickup_persons(backend_conn, phone=bad_phone)

    assert make_pickup_persons(backend_conn, phone="0912-000-001")["phone"] == "0912-000-001"
    assert make_pickup_persons(backend_conn, phone="+886 912000001")["phone"] == "+886 912000001"


def test_pickup_persons_fk(backend_conn: Conn) -> None:
    parent = make_parent_accounts(backend_conn)
    row = make_pickup_persons(backend_conn, created_by_parent_id=parent["id"])

    with pg_error(backend_conn, FK_VIOLATION):
        backend_conn.execute("delete from public.students where id = %s", (row["student_id"],))

    backend_conn.execute("delete from public.parent_accounts where id = %s", (parent["id"],))
    after = backend_conn.execute(
        "select created_by_parent_id from public.pickup_persons where id = %s", (row["id"],)
    ).fetchone()
    assert after == (None,)


def test_pickup_persons_defaults(backend_conn: Conn) -> None:
    row = make_pickup_persons(backend_conn)

    assert row["photo_path"] is None
    assert row["archived_at"] is None
    assert row["created_by_parent_id"] is None


def test_pickup_persons_grants(owner_conn: Conn, backend_conn: Conn) -> None:
    assert_backend_grants(owner_conn, "public.pickup_persons")

    row = make_pickup_persons(backend_conn)
    assert_backend_read_write(backend_conn, "public.pickup_persons", row, relation="鄰居")


def test_pickup_persons_updated_at_trigger(backend_conn: Conn) -> None:
    row = make_pickup_persons(backend_conn, updated_at=SEEDED_UPDATED_AT)

    assert_updated_at_trigger(backend_conn, "public.pickup_persons", row["id"], relation="鄰居")
