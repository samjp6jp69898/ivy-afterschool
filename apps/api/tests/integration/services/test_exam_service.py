"""BACKEND-452 / 464 / 465 / 466 / 453 / 454：exam_service。

應考名單、歷次成績、家長端成績、考試列表與詳情。
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.pagination import PageParams
from app.models.exams import Exam
from app.models.reference import ExamType, Subject
from app.schemas.exams import ExamListQuery, ExamOut
from app.services.exam_service import (
    get_child_exam_detail,
    get_exam,
    get_exam_or_404,
    get_student_exam_history,
    list_child_exams,
    list_exams,
    resolve_exam_roster,
)
from tests.support.factories import (
    make_class,
    make_exam,
    make_exam_score,
    make_exam_subject,
    make_staff,
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


# --- BACKEND-453 list_exams ---

_ALL = PageParams(page=1, page_size=200)


def _exam_type(db: Session, name: str) -> ExamType:
    return db.execute(select(ExamType).where(ExamType.name == name)).scalar_one()


def _listed(db: Session, ids: set[UUID], **filters: object) -> list[ExamOut]:
    """只看本測試建立的考試，避免 DB 內其他資料干擾。"""
    page = list_exams(db, ExamListQuery.model_validate(filters), _ALL)
    return [exam for exam in page.items if exam.id in ids]


def test_list_exams_filters(db_session: Session) -> None:
    class_a = make_class(db_session, name="A班", grade_levels=(3, 4))
    sept = make_exam(
        db_session,
        name="九月小考",
        exam_type=_exam_type(db_session, "小考"),
        exam_date=date(2026, 9, 30),
        grade_level=3,
    )
    mid = make_exam(
        db_session,
        name="第一次段考",
        exam_date=date(2026, 10, 15),
        grade_level=None,
        class_=class_a,
        status="published",
    )
    late = make_exam(db_session, name="第二次段考", exam_date=date(2026, 10, 20), grade_level=4)
    ids = {sept.id, mid.id, late.id}

    def got(**filters: object) -> list[UUID]:
        return [exam.id for exam in _listed(db_session, ids, **filters)]

    assert got() == [late.id, mid.id, sept.id]  # exam_date 新到舊
    assert got(status="draft") == [late.id, sept.id]
    assert got(status="published") == [mid.id]
    assert got(grade_level=3) == [sept.id]
    assert got(q="段考") == [late.id, mid.id]
    assert got(q="第一次") == [mid.id]
    assert got(class_id=class_a.id) == [mid.id]
    assert got(exam_type_id=_exam_type(db_session, "小考").id) == [sept.id]
    assert got(date_from=date(2026, 10, 15), date_to=date(2026, 10, 19)) == [mid.id]
    assert got(date_from=date(2026, 10, 16)) == [late.id]
    assert got(date_to=date(2026, 9, 30)) == [sept.id]
    # q 的 % / _ 視為一般字元
    assert got(q="%") == []


def test_list_exams_fields(db_session: Session) -> None:
    class_a = make_class(db_session, name="A班")
    director = make_staff(db_session, display_name="陳主任")
    exam = make_exam(db_session, grade_level=None, class_=class_a, status="published")
    exam.published_by = director.id
    exam.note = "範圍第一到三課"
    make_exam_subject(db_session, exam, _subject(db_session, "數學"), sort_order=20)
    make_exam_subject(
        db_session, exam, _subject(db_session, "國語"), sort_order=10, full_score=Decimal("50")
    )
    db_session.flush()
    db_session.expire_all()

    [out] = _listed(db_session, {exam.id})

    assert out.name == "第一次段考"
    assert (out.exam_type.name, out.status, out.note) == ("段考", "published", "範圍第一到三課")
    assert out.class_ is not None
    assert (out.class_.id, out.class_.name) == (class_a.id, "A班")
    assert out.published_by_name == "陳主任"
    assert out.published_at == datetime(2026, 8, 1, tzinfo=UTC)
    assert [(s.subject_name, s.full_score) for s in out.subjects] == [
        ("國語", Decimal("50")),
        ("數學", Decimal("100")),
    ]


def test_list_exams_same_date_newest_created_first(db_session: Session) -> None:
    older = make_exam(db_session, name="甲")
    newer = make_exam(db_session, name="乙")
    older.created_at = datetime(2026, 8, 8, tzinfo=UTC)
    newer.created_at = datetime(2026, 8, 9, tzinfo=UTC)
    db_session.flush()

    assert [e.id for e in _listed(db_session, {older.id, newer.id})] == [newer.id, older.id]


def test_list_exams_pagination(db_session: Session) -> None:
    exams = [
        make_exam(db_session, name=f"分頁考試{i}", exam_date=date(2026, 9, 1 + i)) for i in range(3)
    ]

    page = list_exams(db_session, ExamListQuery(q="分頁考試"), PageParams(page=2, page_size=2))

    assert page.total == 3
    assert [e.id for e in page.items] == [exams[0].id]


def test_list_exams_roster_count(db_session: Session) -> None:
    class_a = make_class(db_session, name="A班", grade_levels=(4, 6))
    class_b = make_class(db_session, name="B班", grade_levels=(3,))
    grade = make_exam(db_session, name="六年級段考", grade_level=6)
    by_class = make_exam(db_session, name="A班段考", grade_level=None, class_=class_a)
    both = make_exam(db_session, name="A班六年級段考", grade_level=6, class_=class_a)
    nobody = make_exam(db_session, name="無人段考", grade_level=None, class_=class_b)
    make_student(db_session, name="王小明", grade_level=6, class_=class_a)
    make_student(db_session, name="陳小華", grade_level=6, class_=None)
    make_student(db_session, name="林小安", grade_level=4, class_=class_a)
    make_student(db_session, name="張小美", grade_level=6, class_=class_a, status="suspended")
    make_student(db_session, name="趙小雅", grade_level=6, archived=True)

    counts = {
        e.id: e.roster_count
        for e in _listed(db_session, {grade.id, by_class.id, both.id, nobody.id})
    }

    assert counts == {grade.id: 2, by_class.id: 2, both.id: 1, nobody.id: 0}


def test_list_exams_query_count(db_session: Session) -> None:
    class_a = make_class(db_session, grade_levels=(3,))
    director = make_staff(db_session, display_name="陳主任")
    for index in range(10):
        exam = make_exam(
            db_session,
            name=f"計數考試{index}",
            class_=class_a if index % 2 else None,
            grade_level=3,
            status="published" if index % 3 == 0 else "draft",
        )
        if exam.status == "published":
            exam.published_by = director.id
        make_exam_subject(db_session, exam, _subject(db_session, "國語"))
        make_exam_subject(db_session, exam, _subject(db_session, "數學"), sort_order=1)
    for _ in range(5):
        make_student(db_session, grade_level=3, class_=class_a)
    db_session.flush()
    db_session.expire_all()
    statements: list[str] = []

    def record(_conn: object, _cur: object, statement: str, *_args: object) -> None:
        statements.append(statement)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", record)
    try:
        page = list_exams(db_session, ExamListQuery(q="計數考試"), _ALL)
    finally:
        event.remove(engine, "before_cursor_execute", record)

    assert page.total == 10
    assert all(len(e.subjects) == 2 for e in page.items)
    assert {e.published_by_name for e in page.items} == {"陳主任", None}
    assert len(statements) <= 5


# --- BACKEND-454 get_exam / get_exam_or_404 ---


def test_get_exam_detail(db_session: Session) -> None:
    exam = _exam_with_subjects(db_session, "國語", "數學", grade_level=6)
    make_student(db_session, name="王小明", grade_level=6)
    make_student(db_session, name="陳小華", grade_level=6)
    make_student(db_session, name="林小安", grade_level=5)
    db_session.expire_all()

    out = get_exam(db_session, exam.id)

    assert out.id == exam.id
    assert [s.subject_name for s in out.subjects] == ["國語", "數學"]
    assert [s.sort_order for s in out.subjects] == [10, 20]
    assert out.roster_count == 2
    assert (out.grade_level, out.class_) == (6, None)
    assert out.published_by_name is None
    assert out.model_dump(by_alias=True)["class"] is None


def test_get_exam_not_found(db_session: Session) -> None:
    with pytest.raises(AppError) as missing:
        get_exam(db_session, uuid4())
    with pytest.raises(AppError) as missing_row:
        get_exam_or_404(db_session, uuid4(), for_update=True)

    assert (missing.value.status, missing.value.code) == (404, "exam_not_found")
    assert (missing_row.value.status, missing_row.value.code) == (404, "exam_not_found")


@pytest.mark.parametrize(
    ("kwargs", "clause"),
    [
        ({}, None),
        ({"for_update": True}, "FOR UPDATE OF exams"),
        ({"for_share": True}, "FOR SHARE OF exams"),
    ],
)
def test_get_exam_or_404_lock_clause(
    db_session: Session, kwargs: dict[str, bool], clause: str | None
) -> None:
    class_a = make_class(db_session)
    exam = make_exam(db_session, grade_level=None, class_=class_a)
    statements: list[str] = []

    def record(_conn: object, _cur: object, statement: str, *_args: object) -> None:
        statements.append(statement)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", record)
    try:
        row = get_exam_or_404(db_session, exam.id, **kwargs)
    finally:
        event.remove(engine, "before_cursor_execute", record)

    assert row.id == exam.id
    exam_selects = [s for s in statements if "FROM exams" in s]
    assert exam_selects
    if clause is None:
        assert all(" FOR " not in s for s in exam_selects)
    else:
        assert clause in exam_selects[0]


def test_get_exam_or_404_rejects_both_locks(db_session: Session) -> None:
    exam = make_exam(db_session)
    with pytest.raises(ValueError, match="for_update"):
        get_exam_or_404(db_session, exam.id, for_update=True, for_share=True)
