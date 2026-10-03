"""DB-012：classes 表（同學年度未封存班名唯一、grade_levels 值域、學年度範圍，RLS、updated_at）。"""

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
from tests.integration.db.factories import make_classes


def test_classes_name_unique_per_year(backend_conn: Conn) -> None:
    make_classes(backend_conn, academic_year=115, name="A班")

    with pg_error(backend_conn, UNIQUE_VIOLATION) as err:
        make_classes(backend_conn, academic_year=115, name=" a班")
    assert err.constraint_name == "uq_classes_year_name"

    assert make_classes(backend_conn, academic_year=116, name="A班")["academic_year"] == 116


def test_classes_archived_name_reusable(backend_conn: Conn) -> None:
    old = make_classes(backend_conn, academic_year=115, name="A班")
    backend_conn.execute(
        "update public.classes set archived_at = now() where id = %s", (old["id"],)
    )

    new = make_classes(backend_conn, academic_year=115, name="A班")

    assert new["id"] != old["id"]
    assert new["archived_at"] is None


def test_classes_grade_levels_domain(backend_conn: Conn) -> None:
    with pg_error(backend_conn, CHECK_VIOLATION) as err:
        make_classes(backend_conn, grade_levels=[7])
    assert err.constraint_name == "ck_classes_grade_levels"
    for grade_levels in ([0], [], [1, None]):
        with pg_error(backend_conn, CHECK_VIOLATION) as err:
            make_classes(backend_conn, grade_levels=grade_levels)
        assert err.constraint_name == "ck_classes_grade_levels", grade_levels

    assert make_classes(backend_conn, name="低年級", grade_levels=[1, 2])["grade_levels"] == [1, 2]
    assert make_classes(backend_conn, name="六年級", grade_levels=[6])["grade_levels"] == [6]


def test_classes_academic_year_range(backend_conn: Conn) -> None:
    for academic_year in (2026, 99):
        with pg_error(backend_conn, CHECK_VIOLATION):
            make_classes(backend_conn, academic_year=academic_year)

    for academic_year in (100, 200):
        row = make_classes(backend_conn, academic_year=academic_year)
        assert row["academic_year"] == academic_year


def test_classes_name_not_blank(backend_conn: Conn) -> None:
    for name in ("  ", "班" * 31):
        with pg_error(backend_conn, CHECK_VIOLATION):
            make_classes(backend_conn, name=name)

    assert make_classes(backend_conn, name="班" * 30)["name"] == "班" * 30


def test_classes_defaults(backend_conn: Conn) -> None:
    row = make_classes(backend_conn, name="低年級 A 班", grade_levels=[1, 2], academic_year=115)

    assert row["sort_order"] == 0
    assert row["archived_at"] is None


def test_classes_grants(owner_conn: Conn, backend_conn: Conn) -> None:
    assert_backend_grants(owner_conn, "public.classes")

    row = make_classes(backend_conn)
    assert_backend_read_write(backend_conn, "public.classes", row, sort_order=5)


def test_classes_updated_at_trigger(backend_conn: Conn) -> None:
    row = make_classes(backend_conn, updated_at=SEEDED_UPDATED_AT)

    assert_updated_at_trigger(backend_conn, "public.classes", row["id"], sort_order=5)
