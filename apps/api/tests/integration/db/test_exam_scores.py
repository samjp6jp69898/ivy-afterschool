"""DB-028：exam_scores 表（唯一、分數範圍 trigger、缺考互斥、複合 FK、滿分下修保護）。

每條 SQLSTATE 反例只違反一個約束，並以 constraint 名稱斷言是哪一條擋下。
"""

from decimal import Decimal
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
    connect_backend,
    connect_owner,
    pg_error,
)
from tests.integration.db.factories import (
    make_exam_scores,
    make_exam_subjects,
    make_exam_types,
    make_exams,
    make_staff_users,
    make_students,
    make_subjects,
)

WITHIN_FULL_SCORE = "ck_exam_scores_within_full_score"
FULL_SCORE_FLOOR = "ck_exam_subjects_full_score_floor"


def _count(conn: Conn, exam_id: object, subject_id: object) -> int:
    row = conn.execute(
        "select count(*) from public.exam_scores where exam_id = %s and subject_id = %s",
        (exam_id, subject_id),
    ).fetchone()
    assert row is not None
    return row[0]


def test_exam_scores_unique_triplet(backend_conn: Conn) -> None:
    first = make_exam_scores(backend_conn)

    with pg_error(backend_conn, UNIQUE_VIOLATION) as err:
        make_exam_scores(
            backend_conn,
            exam_id=first["exam_id"],
            subject_id=first["subject_id"],
            student_id=first["student_id"],
        )
    assert err.constraint_name == "uq_exam_scores_exam_student_subject"

    other_student = make_students(backend_conn)
    make_exam_scores(
        backend_conn,
        exam_id=first["exam_id"],
        subject_id=first["subject_id"],
        student_id=other_student["id"],
    )


def test_exam_scores_upsert_keeps_single_row(backend_conn: Conn) -> None:
    first = make_exam_scores(backend_conn, score=Decimal("70"))

    backend_conn.execute(
        """
        insert into public.exam_scores (exam_id, student_id, subject_id, score)
        values (%s, %s, %s, 88)
        on conflict (exam_id, student_id, subject_id) do update set score = 88
        """,
        (first["exam_id"], first["student_id"], first["subject_id"]),
    )

    rows = backend_conn.execute(
        "select score from public.exam_scores where exam_id = %s and subject_id = %s",
        (first["exam_id"], first["subject_id"]),
    ).fetchall()
    assert rows == [(Decimal("88.00"),)]


def test_exam_scores_score_range(backend_conn: Conn) -> None:
    subject = make_exam_subjects(backend_conn, full_score=Decimal("100"))
    key = {"exam_id": subject["exam_id"], "subject_id": subject["subject_id"]}

    with pg_error(backend_conn, CHECK_VIOLATION) as err:
        make_exam_scores(backend_conn, **key, score=Decimal("-1"))
    assert err.constraint_name == "exam_scores_score_check"

    with pg_error(backend_conn, CHECK_VIOLATION) as err:
        make_exam_scores(backend_conn, **key, score=Decimal("100.5"))
    assert err.constraint_name == WITHIN_FULL_SCORE

    assert make_exam_scores(backend_conn, **key, score=Decimal("100"))["score"] == 100
    assert make_exam_scores(backend_conn, **key, score=Decimal("0"))["score"] == 0

    low = make_exam_subjects(backend_conn, full_score=Decimal("50"))
    with pg_error(backend_conn, CHECK_VIOLATION) as err:
        make_exam_scores(
            backend_conn,
            exam_id=low["exam_id"],
            subject_id=low["subject_id"],
            score=Decimal("60"),
        )
    assert err.constraint_name == WITHIN_FULL_SCORE


def test_exam_scores_score_range_on_update(backend_conn: Conn) -> None:
    row = make_exam_scores(backend_conn, score=Decimal("90"))

    with pg_error(backend_conn, CHECK_VIOLATION) as err:
        backend_conn.execute(
            "update public.exam_scores set score = 100.01 where id = %s", (row["id"],)
        )
    assert err.constraint_name == WITHIN_FULL_SCORE

    # 改掛到滿分較低的科目也要重新檢查
    low = make_exam_subjects(backend_conn, exam_id=row["exam_id"], full_score=Decimal("50"))
    with pg_error(backend_conn, CHECK_VIOLATION) as err:
        backend_conn.execute(
            "update public.exam_scores set subject_id = %s where id = %s",
            (low["subject_id"], row["id"]),
        )
    assert err.constraint_name == WITHIN_FULL_SCORE


def test_exam_scores_absent_excludes_score(backend_conn: Conn) -> None:
    with pg_error(backend_conn, CHECK_VIOLATION) as err:
        make_exam_scores(backend_conn, is_absent=True, score=Decimal("80"))
    assert err.constraint_name == "ck_exam_scores_absent_no_score"

    absent = make_exam_scores(backend_conn, is_absent=True, score=None)
    assert (absent["is_absent"], absent["score"]) == (True, None)


def test_exam_scores_note_length(backend_conn: Conn) -> None:
    with pg_error(backend_conn, CHECK_VIOLATION) as err:
        make_exam_scores(backend_conn, note="註" * 201)
    assert err.constraint_name == "exam_scores_note_check"

    assert make_exam_scores(backend_conn, note="註" * 200)["note"] == "註" * 200


def test_exam_scores_subject_must_belong_to_exam(backend_conn: Conn) -> None:
    exam_subject = make_exam_subjects(backend_conn)
    other_subject = make_exam_subjects(backend_conn)  # 另一場考試的科目

    with pg_error(backend_conn, FK_VIOLATION) as err:
        make_exam_scores(
            backend_conn,
            exam_id=exam_subject["exam_id"],
            subject_id=other_subject["subject_id"],
            score=Decimal("80"),
        )
    assert err.constraint_name == "fk_exam_scores_exam_subject"


def test_exam_scores_updated_by_set_null(backend_conn: Conn) -> None:
    staff = make_staff_users(backend_conn)
    row = make_exam_scores(backend_conn, updated_by=staff["id"])

    backend_conn.execute("delete from public.staff_users where id = %s", (staff["id"],))

    after = backend_conn.execute(
        "select updated_by from public.exam_scores where id = %s", (row["id"],)
    ).fetchone()
    assert after == (None,)


def test_exam_scores_student_restrict(backend_conn: Conn) -> None:
    row = make_exam_scores(backend_conn)

    with pg_error(backend_conn, FK_VIOLATION):
        backend_conn.execute("delete from public.students where id = %s", (row["student_id"],))


def test_exam_scores_cascade_on_subject_removal(backend_conn: Conn) -> None:
    row = make_exam_scores(backend_conn, score=Decimal("90"))

    backend_conn.execute(
        "delete from public.exam_subjects where exam_id = %s and subject_id = %s",
        (row["exam_id"], row["subject_id"]),
    )

    assert _count(backend_conn, row["exam_id"], row["subject_id"]) == 0


def test_exam_scores_cascade_on_exam_removal(backend_conn: Conn) -> None:
    row = make_exam_scores(backend_conn, score=Decimal("90"))

    backend_conn.execute("delete from public.exams where id = %s", (row["exam_id"],))

    assert _count(backend_conn, row["exam_id"], row["subject_id"]) == 0


def test_exam_scores_block_full_score_lowering(backend_conn: Conn) -> None:
    row = make_exam_scores(backend_conn, score=Decimal("90"))
    lower = "update public.exam_subjects set full_score = %s where exam_id = %s and subject_id = %s"
    key = (row["exam_id"], row["subject_id"])

    with pg_error(backend_conn, CHECK_VIOLATION) as err:
        backend_conn.execute(lower, (80, *key))
    assert err.constraint_name == FULL_SCORE_FLOOR

    # 剛好等於既有最高分可以；調高也可以
    backend_conn.execute(lower, (90, *key))
    backend_conn.execute(lower, (95, *key))
    full = backend_conn.execute(
        "select full_score from public.exam_subjects where exam_id = %s and subject_id = %s", key
    ).fetchone()
    assert full == (Decimal("95.00"),)


def test_exam_scores_lowering_ignores_other_subjects_and_null_scores(backend_conn: Conn) -> None:
    row = make_exam_scores(backend_conn, score=Decimal("90"))
    other = make_exam_subjects(backend_conn, exam_id=row["exam_id"], full_score=Decimal("100"))
    make_exam_scores(
        backend_conn, exam_id=row["exam_id"], subject_id=other["subject_id"], is_absent=True
    )

    # 另一科的成績與缺考列都不影響這一科下修
    backend_conn.execute(
        "update public.exam_subjects set full_score = 10 where exam_id = %s and subject_id = %s",
        (other["exam_id"], other["subject_id"]),
    )


def test_exam_scores_grants(owner_conn: Conn, backend_conn: Conn) -> None:
    assert_backend_grants(owner_conn, "public.exam_scores")

    row = make_exam_scores(backend_conn)
    assert_backend_read_write(
        backend_conn, "public.exam_scores", row, score=Decimal("75.5"), note="訂正後"
    )


def test_exam_scores_updated_at_trigger(backend_conn: Conn) -> None:
    row = make_exam_scores(backend_conn, updated_at=SEEDED_UPDATED_AT)

    assert_updated_at_trigger(backend_conn, "public.exam_scores", row["id"], note="訂正後")


LOCK_NOT_AVAILABLE = "55P03"


def test_exam_scores_full_score_lowering_waits_for_concurrent_score() -> None:
    """登分 transaction 未結束時，下修滿分要等它（不會讓「分數 > 滿分」同時成立）。

    資料必須 commit 才看得到：兩條 app_backend 連線，結束後以 owner 清除自己建的測資。
    """
    conn_setup, conn_score, conn_lower = connect_backend(), connect_backend(), connect_backend()
    exam_type_id = exam_id = subject_id = student_id = None
    try:
        with conn_setup.transaction():
            exam_type_id = make_exam_types(conn_setup, name=f"類型{uuid4().hex[:8]}")["id"]
            exam_id = make_exams(conn_setup, exam_type_id=exam_type_id)["id"]
            subject_id = make_subjects(conn_setup, name=f"科目{uuid4().hex[:8]}")["id"]
            make_exam_subjects(
                conn_setup, exam_id=exam_id, subject_id=subject_id, full_score=Decimal("100")
            )
            student_id = make_students(conn_setup)["id"]

        make_exam_scores(
            conn_score,
            exam_id=exam_id,
            subject_id=subject_id,
            student_id=student_id,
            score=Decimal("80"),
        )  # 未 commit，持有 exam_subjects 列的共享鎖

        conn_lower.execute("set lock_timeout = '300ms'")
        lower = "update public.exam_subjects set full_score = 70 where exam_id = %s"
        with pg_error(conn_lower, LOCK_NOT_AVAILABLE):
            conn_lower.execute(lower, (exam_id,))

        conn_score.commit()
        with pg_error(conn_lower, CHECK_VIOLATION) as err:
            conn_lower.execute(lower, (exam_id,))
        assert err.constraint_name == FULL_SCORE_FLOOR
    finally:
        for conn in (conn_score, conn_lower, conn_setup):
            conn.rollback()
            conn.close()
        with connect_owner() as owner:
            if exam_id is not None:
                owner.execute("delete from public.exams where id = %s", (exam_id,))
            if student_id is not None:
                owner.execute("delete from public.students where id = %s", (student_id,))
            if subject_id is not None:
                owner.execute("delete from public.subjects where id = %s", (subject_id,))
            if exam_type_id is not None:
                owner.execute("delete from public.exam_types where id = %s", (exam_type_id,))
            owner.commit()
