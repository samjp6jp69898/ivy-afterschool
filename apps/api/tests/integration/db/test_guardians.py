"""DB-016：guardians 表（每生一位 primary、家長綁定唯一、relation 值域、FK、預設值、updated_at）。"""

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
from tests.integration.db.factories import make_guardians, make_parent_accounts, make_students


def test_guardians_one_primary_per_student(backend_conn: Conn) -> None:
    first = make_guardians(backend_conn, is_primary=True)

    with pg_error(backend_conn, UNIQUE_VIOLATION) as err:
        make_guardians(backend_conn, student_id=first["student_id"], is_primary=True)
    assert err.constraint_name == "uq_guardians_one_primary"

    # 不同學生、或同一學生的非 primary 不受影響
    make_guardians(backend_conn, is_primary=True)
    make_guardians(backend_conn, student_id=first["student_id"], is_primary=False)


def test_guardians_primary_allowed_after_archive(backend_conn: Conn) -> None:
    first = make_guardians(backend_conn, is_primary=True)
    backend_conn.execute(
        "update public.guardians set archived_at = now() where id = %s", (first["id"],)
    )

    second = make_guardians(backend_conn, student_id=first["student_id"], is_primary=True)
    assert second["is_primary"] is True


def test_guardians_parent_bound_once_per_student(backend_conn: Conn) -> None:
    parent = make_parent_accounts(backend_conn)
    first = make_guardians(backend_conn, parent_account_id=parent["id"])

    with pg_error(backend_conn, UNIQUE_VIOLATION) as err:
        make_guardians(backend_conn, student_id=first["student_id"], parent_account_id=parent["id"])
    assert err.constraint_name == "uq_guardians_student_parent"

    other_student = make_students(backend_conn)
    assert (
        make_guardians(
            backend_conn, student_id=other_student["id"], parent_account_id=parent["id"]
        )["parent_account_id"]
        == parent["id"]
    )


def test_guardians_relation_domain(backend_conn: Conn) -> None:
    with pg_error(backend_conn, CHECK_VIOLATION):
        make_guardians(backend_conn, relation="uncle")


def test_guardians_fk_behaviour(backend_conn: Conn) -> None:
    parent = make_parent_accounts(backend_conn)
    row = make_guardians(backend_conn, parent_account_id=parent["id"])

    with pg_error(backend_conn, FK_VIOLATION):
        backend_conn.execute("delete from public.students where id = %s", (row["student_id"],))

    backend_conn.execute("delete from public.parent_accounts where id = %s", (parent["id"],))
    after = backend_conn.execute(
        "select parent_account_id from public.guardians where id = %s", (row["id"],)
    ).fetchone()
    assert after == (None,)


def test_guardians_defaults(backend_conn: Conn) -> None:
    row = make_guardians(backend_conn)

    assert row["is_primary"] is False
    assert row["can_pickup"] is True
    assert row["receives_notifications"] is True
    assert row["archived_at"] is None


def test_guardians_name_not_blank(backend_conn: Conn) -> None:
    for bad_name in (" ", "王" * 51):
        with pg_error(backend_conn, CHECK_VIOLATION):
            make_guardians(backend_conn, name=bad_name)


def test_guardians_grants(owner_conn: Conn, backend_conn: Conn) -> None:
    assert_backend_grants(owner_conn, "public.guardians")

    row = make_guardians(backend_conn)
    assert_backend_read_write(backend_conn, "public.guardians", row, can_pickup=False)


def test_guardians_updated_at_trigger(backend_conn: Conn) -> None:
    row = make_guardians(backend_conn, updated_at=SEEDED_UPDATED_AT)

    assert_updated_at_trigger(backend_conn, "public.guardians", row["id"], can_pickup=False)
