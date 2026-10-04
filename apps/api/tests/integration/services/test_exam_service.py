"""BACKEND-452 / 464 / 465 / 466：exam_service（應考名單、歷次成績、家長端成績）。"""

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.pagination import PageParams
from app.models.exams import Exam
from app.models.reference import Subject
from app.services.exam_service import (
    get_child_exam_detail,
    get_student_exam_history,
    list_child_exams,
    resolve_exam_roster,
)
from tests.support.factories import (
    make_class,
    make_exam,
    make_exam_score,
    make_exam_subject,
    make_student,
)

_FIRST_PAGE = PageParams(page=1, page_size=20)


def _subject(db: Session, name: str) -> Subject:
    return db.execute(select(Subject).where(Subject.name == name)).scalar_one()


def _exam_with_subjects(db: Session, *names: str, **kwargs: object) -> Exam:
    """依 names 順序建立考試與科目（sort_order 10、20、...，滿分 100）。"""
    exam = make_exam(db, **kwargs)  # type: ignore[arg-type]
    for index, name in enumerate(names, start=1):
        make_exam_subject(db, exam, _subject(db, name), sort_order=index * 10)
    return exam


def _mine(roster: list[UUID], ids: set[UUID]) -> list[UUID]:
    """只看本測試建立的學生，避免 DB 內其他資料干擾。"""
    return [sid for sid in roster if sid in ids]


def test_exam_roster_scopes(db_session: Session) -> None:
    class_a = make_class(db_session, name="A班", grade_levels=(3, 4))
    class_b = make_class(db_session, name="B班", grade_levels=(3,))
    ming = make_student(db_session, name="王小明", grade_level=3, class_=class_a)
    hua = make_student(db_session, name="陳小華", grade_level=4, class_=class_a)
    an = make_student(db_session, name="林小安", grade_level=3, class_=class_b)
    mine = {ming.id, hua.id, an.id}

    by_grade = resolve_exam_roster(db_session, make_exam(db_session, grade_level=3))
    by_class = resolve_exam_roster(
        db_session, make_exam(db_session, grade_level=None, class_=class_a)
    )
    both = resolve_exam_roster(db_session, make_exam(db_session, grade_level=3, class_=class_a))

    assert _mine(by_grade, mine) == [ming.id, an.id]
    assert by_class == [ming.id, hua.id]
    assert both == [ming.id]


def test_exam_roster_excludes_inactive(db_session: Session) -> None:
    class_a = make_class(db_session, name="A班", grade_levels=(3,))
    ming = make_student(db_session, name="王小明", grade_level=3, class_=class_a)
    # ck_students_withdrawn_status：withdrawn 必須有 withdrawn_on，factory 只能先建 active 再改
    withdrawn = make_student(db_session, name="張小美", grade_level=3, class_=class_a)
    withdrawn.status = "withdrawn"
    withdrawn.withdrawn_on = date(2026, 9, 1)
    db_session.flush()
    make_student(db_session, name="李小龍", grade_level=3, class_=class_a, status="suspended")
    make_student(db_session, name="趙小雅", grade_level=3, class_=class_a, archived=True)

    roster = resolve_exam_roster(
        db_session, make_exam(db_session, grade_level=None, class_=class_a)
    )

    assert roster == [ming.id]


def test_student_exam_history(db_session: Session) -> None:
    chinese, math = _subject(db_session, "國語"), _subject(db_session, "數學")
    english = _subject(db_session, "英語")
    ming = make_student(db_session, name="王小明")
    other = make_student(db_session, name="林小安")
    draft = _exam_with_subjects(db_session, "國語", name="小考", exam_date=date(2026, 9, 30))
    published = _exam_with_subjects(
        db_session,
        "國語",
        "數學",
        "英語",
        name="第一次段考",
        exam_date=date(2026, 10, 15),
        status="published",
    )
    unrelated = _exam_with_subjects(
        db_session, "國語", name="別班小考", exam_date=date(2026, 10, 20)
    )
    make_exam_score(db_session, draft, ming, chinese, score=Decimal("95"))
    make_exam_score(db_session, published, ming, chinese, score=Decimal("88"))
    make_exam_score(db_session, published, ming, math, score=None, is_absent=True)
    make_exam_score(db_session, published, other, english, score=Decimal("70"))
    make_exam_score(db_session, unrelated, other, chinese, score=Decimal("60"))

    history = get_student_exam_history(db_session, ming.id)

    assert [(h.exam_id, h.exam_date, h.status) for h in history] == [
        (published.id, date(2026, 10, 15), "published"),
        (draft.id, date(2026, 9, 30), "draft"),
    ]
    assert history[0].exam_name == "第一次段考"
    assert history[0].exam_type_name == "段考"
    assert [(s.subject_name, s.score, s.is_absent) for s in history[0].scores] == [
        ("國語", Decimal("88"), False),
        ("數學", None, True),
        ("英語", None, False),  # 他人的英語分數不洩漏，該生未登分
    ]
    assert history[0].scores[0].full_score == Decimal("100")
    assert [(s.subject_name, s.score) for s in history[1].scores] == [("國語", Decimal("95"))]


def test_student_exam_history_archived_and_missing(db_session: Session) -> None:
    chinese = _subject(db_session, "國語")
    archived = make_student(db_session, name="趙小雅", archived=True)
    exam = _exam_with_subjects(db_session, "國語")
    make_exam_score(db_session, exam, archived, chinese, score=Decimal("80"))

    history = get_student_exam_history(db_session, archived.id)

    assert [(h.exam_id, h.scores[0].score) for h in history] == [(exam.id, Decimal("80"))]
    with pytest.raises(AppError) as exc:
        get_student_exam_history(db_session, uuid4())
    assert (exc.value.status, exc.value.code) == (404, "student_not_found")


def test_list_child_exams(db_session: Session) -> None:
    chinese = _subject(db_session, "國語")
    ming = make_student(db_session, name="王小明")
    published_a = _exam_with_subjects(db_session, "國語", "數學", name="A", status="published")
    draft_b = _exam_with_subjects(db_session, "國語", name="B")
    published_c = _exam_with_subjects(db_session, "國語", name="C", status="published")
    make_exam_score(db_session, published_a, ming, chinese)
    make_exam_score(db_session, draft_b, ming, chinese)
    other = make_student(db_session, name="林小安")
    make_exam_score(db_session, published_c, other, chinese)  # C 只有別人的成績

    page = list_child_exams(db_session, ming.id, _FIRST_PAGE)

    assert page.total == 1
    assert [(i.exam_id, i.name, i.exam_type_name, i.subject_count) for i in page.items] == [
        (published_a.id, "A", "段考", 2)
    ]
    assert page.items[0].exam_date == published_a.exam_date
    assert page.items[0].published_at == published_a.published_at


def test_list_child_exams_order(db_session: Session) -> None:
    chinese = _subject(db_session, "國語")
    ming = make_student(db_session, name="王小明")
    older = _exam_with_subjects(db_session, "國語", exam_date=date(2026, 9, 1), status="published")
    newer = _exam_with_subjects(db_session, "國語", exam_date=date(2026, 10, 1), status="published")
    absent_only = _exam_with_subjects(
        db_session, "國語", exam_date=date(2026, 9, 15), status="published"
    )
    make_exam_score(db_session, older, ming, chinese)
    make_exam_score(db_session, newer, ming, chinese)
    make_exam_score(db_session, absent_only, ming, chinese, score=None, is_absent=True)

    page = list_child_exams(db_session, ming.id, _FIRST_PAGE)
    second = list_child_exams(db_session, ming.id, PageParams(page=2, page_size=2))

    assert [i.exam_id for i in page.items] == [newer.id, absent_only.id, older.id]  # 缺考也算
    assert (second.total, [i.exam_id for i in second.items]) == (3, [older.id])


def test_child_exam_detail(db_session: Session) -> None:
    chinese, math = _subject(db_session, "國語"), _subject(db_session, "數學")
    ming = make_student(db_session, name="王小明")
    other = make_student(db_session, name="林小安")
    exam = _exam_with_subjects(db_session, "國語", "數學", "英語", status="published")
    exam.note = "含作文"
    chinese_score = make_exam_score(db_session, exam, ming, chinese, score=Decimal("95"))
    chinese_score.note = "進步很多"
    make_exam_score(db_session, exam, ming, math, score=None, is_absent=True)
    make_exam_score(db_session, exam, other, math, score=Decimal("40"))
    db_session.flush()

    detail = get_child_exam_detail(db_session, ming.id, exam.id)

    assert (detail.exam_id, detail.name, detail.exam_type_name, detail.note) == (
        exam.id,
        exam.name,
        "段考",
        "含作文",
    )
    assert detail.exam_date == exam.exam_date
    assert [
        (s.subject_name, s.full_score, s.score, s.is_absent, s.note) for s in detail.subjects
    ] == [
        ("國語", Decimal("100"), Decimal("95"), False, "進步很多"),
        ("數學", Decimal("100"), None, True, None),
        ("英語", Decimal("100"), None, False, None),
    ]


def test_child_exam_detail_hidden(db_session: Session) -> None:
    chinese = _subject(db_session, "國語")
    ming = make_student(db_session, name="王小明")
    other = make_student(db_session, name="林小安")
    draft = _exam_with_subjects(db_session, "國語")
    no_rows = _exam_with_subjects(db_session, "國語", status="published")
    make_exam_score(db_session, draft, ming, chinese)
    make_exam_score(db_session, no_rows, other, chinese)

    errors = []
    for exam_id in (draft.id, no_rows.id, uuid4()):
        with pytest.raises(AppError) as exc:
            get_child_exam_detail(db_session, ming.id, exam_id)
        errors.append((exc.value.status, exc.value.code, exc.value.message))

    assert errors[0] == (404, "exam_not_found", errors[0][2])
    assert errors[0] == errors[1] == errors[2]


def test_student_exam_history_query_count_is_constant(db_session: Session) -> None:
    chinese = _subject(db_session, "國語")
    ming = make_student(db_session, name="王小明")

    def add_exam() -> None:
        exam = _exam_with_subjects(db_session, "國語", "數學")
        make_exam_score(db_session, exam, ming, chinese)
        db_session.expire_all()

    def count_queries() -> int:
        statements: list[str] = []

        def record(_conn: object, _cur: object, statement: str, *_args: object) -> None:
            statements.append(statement)

        engine = db_session.get_bind()
        event.listen(engine, "before_cursor_execute", record)
        try:
            get_student_exam_history(db_session, ming.id)
        finally:
            event.remove(engine, "before_cursor_execute", record)
        return len(statements)

    add_exam()
    one_exam = count_queries()
    for _ in range(4):
        add_exam()
    assert count_queries() == one_exam
