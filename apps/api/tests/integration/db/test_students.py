"""DB-014：students 表（學號、值域、退班日期、FK、加密欄位、HMAC 查重、grant、trigger）。"""

from datetime import date

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
from tests.integration.db.factories import make_classes, make_schools, make_students

HMAC_A = "a" * 64
CIPHERTEXT = b"\x00\x01nonce-and-ciphertext"


def test_students_student_no_unique(backend_conn: Conn) -> None:
    make_students(backend_conn, student_no="S115001")

    with pg_error(backend_conn, UNIQUE_VIOLATION) as err:
        make_students(backend_conn, student_no="S115001")
    assert err.constraint_name == "uq_students_student_no"


def test_students_student_no_format(backend_conn: Conn) -> None:
    for student_no in ("S 115", "", "S" * 21, "S115_001", "學001"):
        with pg_error(backend_conn, CHECK_VIOLATION):
            make_students(backend_conn, student_no=student_no)

    for student_no in ("S115-001", "a" * 20):
        assert make_students(backend_conn, student_no=student_no)["student_no"] == student_no


def test_students_enum_domains(backend_conn: Conn) -> None:
    for overrides in (
        {"gender": "boy"},
        {"status": "graduated"},
        {"grade_level": 0},
        {"grade_level": 7},
    ):
        with pg_error(backend_conn, CHECK_VIOLATION):
            make_students(backend_conn, **overrides)

    for gender in ("male", "female", "other"):
        assert make_students(backend_conn, gender=gender)["gender"] == gender
    for status in ("active", "suspended"):
        assert make_students(backend_conn, status=status)["status"] == status
    for grade_level in (1, 6):
        assert make_students(backend_conn, grade_level=grade_level)["grade_level"] == grade_level


def test_students_name_and_school_class_length(backend_conn: Conn) -> None:
    for overrides in ({"name": "  "}, {"name": "王" * 51}, {"school_class": "班" * 21}):
        with pg_error(backend_conn, CHECK_VIOLATION):
            make_students(backend_conn, **overrides)

    row = make_students(backend_conn, name="王" * 50, school_class="三年二班")
    assert (row["name"], row["school_class"]) == ("王" * 50, "三年二班")


def test_students_withdrawn_requires_date(backend_conn: Conn) -> None:
    with pg_error(backend_conn, CHECK_VIOLATION) as err:
        make_students(backend_conn, status="withdrawn", withdrawn_on=None)
    assert err.constraint_name == "ck_students_withdrawn_status"

    row = make_students(backend_conn, status="withdrawn", withdrawn_on=date(2026, 9, 30))
    assert (row["status"], row["withdrawn_on"]) == ("withdrawn", date(2026, 9, 30))


def test_students_withdrawn_on_not_before_enrolled(backend_conn: Conn) -> None:
    with pg_error(backend_conn, CHECK_VIOLATION) as err:
        make_students(backend_conn, enrolled_on=date(2026, 9, 1), withdrawn_on=date(2026, 8, 31))
    assert err.constraint_name == "ck_students_withdrawn_on"

    row = make_students(backend_conn, enrolled_on=date(2026, 9, 1), withdrawn_on=date(2026, 9, 1))
    assert row["withdrawn_on"] == date(2026, 9, 1)


def test_students_school_restrict_class_set_null(backend_conn: Conn) -> None:
    school = make_schools(backend_conn)
    klass = make_classes(backend_conn)
    student = make_students(backend_conn, school_id=school["id"], class_id=klass["id"])

    with pg_error(backend_conn, FK_VIOLATION):
        backend_conn.execute("delete from public.schools where id = %s", (school["id"],))
    backend_conn.execute("delete from public.classes where id = %s", (klass["id"],))

    assert backend_conn.execute(
        "select school_id, class_id from public.students where id = %s", (student["id"],)
    ).fetchone() == (school["id"], None)


def test_students_encrypted_columns_are_bytea(backend_conn: Conn) -> None:
    rows = backend_conn.execute(
        """
        select column_name, data_type from information_schema.columns
        where table_schema = 'public' and table_name = 'students'
          and column_name in ('id_number_enc', 'health_note_enc')
        order by column_name
        """
    ).fetchall()
    assert rows == [("health_note_enc", "bytea"), ("id_number_enc", "bytea")]


def test_students_id_number_hmac_unique(backend_conn: Conn) -> None:
    make_students(backend_conn, id_number_enc=CIPHERTEXT, id_number_hmac=HMAC_A)

    with pg_error(backend_conn, UNIQUE_VIOLATION) as err:
        make_students(backend_conn, id_number_enc=CIPHERTEXT, id_number_hmac=HMAC_A)
    assert err.constraint_name == "uq_students_id_number_hmac"

    first = make_students(backend_conn, id_number_enc=None, id_number_hmac=None)
    second = make_students(backend_conn, id_number_enc=None, id_number_hmac=None)
    assert (first["id_number_hmac"], second["id_number_hmac"]) == (None, None)


def test_students_id_number_hmac_unique_includes_archived(backend_conn: Conn) -> None:
    make_students(
        backend_conn,
        id_number_enc=CIPHERTEXT,
        id_number_hmac=HMAC_A,
        status="withdrawn",
        withdrawn_on=date(2026, 6, 30),
        archived_at=SEEDED_UPDATED_AT,
    )

    with pg_error(backend_conn, UNIQUE_VIOLATION):
        make_students(backend_conn, id_number_enc=CIPHERTEXT, id_number_hmac=HMAC_A)


def test_students_id_number_hmac_format_and_pair(backend_conn: Conn) -> None:
    for id_number_hmac in ("ABC", "A" * 64, "a" * 63):
        with pg_error(backend_conn, CHECK_VIOLATION) as err:
            make_students(backend_conn, id_number_enc=CIPHERTEXT, id_number_hmac=id_number_hmac)
        assert err.constraint_name == "ck_students_id_number_hmac_format", id_number_hmac

    for overrides in (
        {"id_number_enc": CIPHERTEXT, "id_number_hmac": None},
        {"id_number_enc": None, "id_number_hmac": HMAC_A},
    ):
        with pg_error(backend_conn, CHECK_VIOLATION) as err:
            make_students(backend_conn, **overrides)
        assert err.constraint_name == "ck_students_id_number_pair"

    row = make_students(backend_conn, id_number_enc=CIPHERTEXT, id_number_hmac=HMAC_A)
    assert (bytes(row["id_number_enc"]), row["id_number_hmac"]) == (CIPHERTEXT, HMAC_A)


def test_students_defaults(backend_conn: Conn) -> None:
    row = make_students(backend_conn)

    assert row["status"] == "active"
    assert row["archived_at"] is None
    assert (row["id_number_enc"], row["health_note_enc"], row["photo_path"]) == (None, None, None)


def test_students_grants(owner_conn: Conn, backend_conn: Conn) -> None:
    assert_backend_grants(owner_conn, "public.students")

    row = make_students(backend_conn)
    assert_backend_read_write(backend_conn, "public.students", row, note="喜歡畫畫")


def test_students_updated_at_trigger(backend_conn: Conn) -> None:
    row = make_students(backend_conn, updated_at=SEEDED_UPDATED_AT)

    assert_updated_at_trigger(backend_conn, "public.students", row["id"], grade_level=4)
