"""BACKEND-452 / 464 / 465 / 466 / 453 / 454 / 455 / 457 / 458 / 459 / 462 / 463：exam_service。

應考名單、歷次成績、家長端成績、考試列表與詳情、新增 / 刪除、科目設定、成績格、取消發布、各科統計。
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.orm import Session

from app.api.deps import CurrentStaff
from app.core.errors import AppError
from app.core.pagination import PageParams
from app.core.request_meta import RequestMeta
from app.models.account import StaffUser
from app.models.audit import AuditLog
from app.models.exams import Exam, ExamScore
from app.models.reference import ExamType, Subject
from app.schemas.exams import (
    ExamCreateIn,
    ExamListQuery,
    ExamOut,
    ExamSubjectIn,
    ExamSubjectsPutIn,
)
from app.services.exam_service import (
    create_exam,
    delete_exam,
    get_child_exam_detail,
    get_exam,
    get_exam_or_404,
    get_exam_summary,
    get_score_grid,
    get_student_exam_history,
    list_child_exams,
    list_exams,
    resolve_exam_roster,
    set_exam_subjects,
    unpublish_exam,
)
from tests.support.factories import (
    ARCHIVED_AT,
    make_class,
    make_exam,
    make_exam_score,
    make_exam_subject,
    make_staff,
    make_student,
)
from tests.support.fake_clock import FakeClock

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


# --- 寫入方法共用 ---

_META = RequestMeta(ip="203.0.113.5", user_agent="pytest", request_id=None)


def _current(staff: StaffUser) -> CurrentStaff:
    return CurrentStaff(
        id=staff.id,
        username=staff.username,
        display_name=staff.display_name,
        role_id=staff.role.id,
        role_code=staff.role.code,
        role_name=staff.role.name,
        permissions=frozenset(staff.role.permissions),
        must_change_password=staff.must_change_password,
        token_version=staff.token_version,
    )


@pytest.fixture
def actor(db_session: Session) -> CurrentStaff:
    return _current(make_staff(db_session, permissions=["exams:write"], display_name="陳主任"))


def _count(db: Session, table: str, exam_id: UUID) -> int:
    column = "id" if table == "exams" else "exam_id"
    count: int = db.execute(
        text(f"select count(*) from public.{table} where {column} = :id"),  # noqa: S608  測試固定表名
        {"id": exam_id},
    ).scalar_one()
    return count


def _error(exc: pytest.ExceptionInfo[AppError]) -> tuple[int, str]:
    return exc.value.status, exc.value.code


# --- BACKEND-455 create_exam ---


def test_create_exam_success(db_session: Session, actor: CurrentStaff) -> None:
    class_a = make_class(db_session, name="A班", grade_levels=(3, 4))
    data = ExamCreateIn(
        name="第一次段考",
        exam_type_id=_exam_type(db_session, "段考").id,
        exam_date=date(2026, 10, 15),
        grade_level=3,
        class_id=class_a.id,
        note="範圍第一到三課",
    )

    out = create_exam(db_session, data, actor=actor)

    assert (out.name, out.status, out.subjects) == ("第一次段考", "draft", [])
    assert (out.exam_type.name, out.exam_date, out.grade_level) == ("段考", date(2026, 10, 15), 3)
    assert out.class_ is not None
    assert out.class_.id == class_a.id
    assert (out.published_at, out.published_by_name, out.note) == (None, None, "範圍第一到三課")
    row = db_session.get(Exam, out.id)
    assert row is not None
    assert row.status == "draft"


def test_create_exam_validation(db_session: Session, actor: CurrentStaff) -> None:
    inactive_type = ExamType(name=f"停用類型-{uuid4().hex[:6]}", is_active=False)
    db_session.add(inactive_type)
    db_session.flush()
    archived = make_class(db_session, archived=True)
    class_a = make_class(db_session, name="A班", grade_levels=(1, 2))
    exam_type_id = _exam_type(db_session, "段考").id

    def create(**fields: Any) -> None:
        base: dict[str, object] = {
            "name": "第一次段考",
            "exam_type_id": exam_type_id,
            "exam_date": date(2026, 10, 15),
        }
        create_exam(db_session, ExamCreateIn.model_validate({**base, **fields}), actor=actor)

    cases: list[tuple[dict[str, Any], str]] = [
        ({"exam_type_id": inactive_type.id, "grade_level": 3}, "invalid_exam_type"),
        ({"exam_type_id": uuid4(), "grade_level": 3}, "invalid_exam_type"),
        ({"class_id": archived.id}, "invalid_class"),
        ({"class_id": uuid4()}, "invalid_class"),
        ({"class_id": class_a.id, "grade_level": 3}, "grade_not_in_class"),
    ]
    for fields, code in cases:
        with pytest.raises(AppError) as exc:
            create(**fields)
        assert _error(exc) == (422, code), fields
    # 班級含該年級 → 可建立
    create(class_id=class_a.id, grade_level=2)


# --- BACKEND-457 delete_exam ---


def test_delete_exam_draft_cascade(db_session: Session, actor: CurrentStaff) -> None:
    exam = _exam_with_subjects(db_session, "國語")
    chinese = _subject(db_session, "國語")
    make_exam_score(db_session, exam, make_student(db_session), chinese)
    make_exam_score(db_session, exam, make_student(db_session, name="陳小華"), chinese)
    exam_id = exam.id

    delete_exam(db_session, exam_id, actor=actor)

    assert (_count(db_session, "exams", exam_id), _count(db_session, "exam_subjects", exam_id)) == (
        0,
        0,
    )
    assert _count(db_session, "exam_scores", exam_id) == 0


def test_delete_exam_published(db_session: Session, actor: CurrentStaff) -> None:
    exam = make_exam(db_session, status="published")

    with pytest.raises(AppError) as published:
        delete_exam(db_session, exam.id, actor=actor)
    with pytest.raises(AppError) as missing:
        delete_exam(db_session, uuid4(), actor=actor)

    assert _error(published) == (409, "exam_published")
    assert _error(missing) == (404, "exam_not_found")
    assert _count(db_session, "exams", exam.id) == 1


# --- BACKEND-458 set_exam_subjects ---


def _put(*items: tuple[Subject, str, int]) -> ExamSubjectsPutIn:
    return ExamSubjectsPutIn(
        items=[
            ExamSubjectIn(subject_id=subject.id, full_score=Decimal(full), sort_order=order)
            for subject, full, order in items
        ]
    )


def test_set_exam_subjects_replace(db_session: Session, actor: CurrentStaff) -> None:
    chinese, math, english = (_subject(db_session, n) for n in ("國語", "數學", "英語"))
    exam = _exam_with_subjects(db_session, "國語", "數學")
    make_exam_score(db_session, exam, make_student(db_session), math, score=Decimal("88"))
    make_exam_score(db_session, exam, make_student(db_session, name="陳小華"), math)
    chinese_score = make_exam_score(db_session, exam, make_student(db_session), chinese)

    out = set_exam_subjects(
        db_session, exam.id, _put((english, "50", 1), (chinese, "100", 0)), actor=actor
    )

    assert [(s.subject_name, s.full_score, s.sort_order) for s in out.subjects] == [
        ("國語", Decimal("100"), 0),
        ("英語", Decimal("50"), 1),
    ]
    math_scores = db_session.execute(
        select(func.count())
        .select_from(ExamScore)
        .where(ExamScore.exam_id == exam.id, ExamScore.subject_id == math.id)
    ).scalar_one()
    assert math_scores == 0
    # 保留的科目成績不受影響
    assert db_session.get(ExamScore, chinese_score.id) is not None
    assert _count(db_session, "exam_subjects", exam.id) == 2


def test_set_exam_subjects_full_score_guard(db_session: Session, actor: CurrentStaff) -> None:
    chinese, math = _subject(db_session, "國語"), _subject(db_session, "數學")
    exam = _exam_with_subjects(db_session, "國語", "數學")
    make_exam_score(db_session, exam, make_student(db_session), math, score=Decimal("92"))
    make_exam_score(db_session, exam, make_student(db_session), math, score=Decimal("70"))
    make_exam_score(db_session, exam, make_student(db_session), chinese, score=Decimal("60"))

    with pytest.raises(AppError) as exc:
        # 國語下修到 80 仍高於最高分 60；數學 90 低於最高分 92
        set_exam_subjects(
            db_session, exam.id, _put((chinese, "80", 10), (math, "90", 20)), actor=actor
        )

    assert _error(exc) == (409, "full_score_below_existing")
    assert exc.value.details == {"subject_id": math.id, "max_score": 92.0}
    out = get_exam(db_session, exam.id)
    assert [(s.subject_name, s.full_score) for s in out.subjects] == [
        ("國語", Decimal("100")),
        ("數學", Decimal("100")),
    ]
    # 失敗後 session 仍可用，且可改成合法的滿分
    ok = set_exam_subjects(db_session, exam.id, _put((math, "92", 0)), actor=actor)
    assert [(s.subject_name, s.full_score) for s in ok.subjects] == [("數學", Decimal("92"))]


def test_set_exam_subjects_published(db_session: Session, actor: CurrentStaff) -> None:
    exam = make_exam(db_session, status="published")

    with pytest.raises(AppError) as exc:
        set_exam_subjects(
            db_session, exam.id, _put((_subject(db_session, "國語"), "100", 0)), actor=actor
        )

    assert _error(exc) == (409, "exam_published")


def test_set_exam_subjects_invalid(db_session: Session, actor: CurrentStaff) -> None:
    inactive = Subject(name=f"停用科目-{uuid4().hex[:6]}", is_active=False)
    db_session.add(inactive)
    db_session.flush()
    exam = _exam_with_subjects(db_session, "國語")
    missing_id = uuid4()

    with pytest.raises(AppError) as exc:
        set_exam_subjects(
            db_session,
            exam.id,
            ExamSubjectsPutIn(
                items=[
                    ExamSubjectIn(subject_id=_subject(db_session, "國語").id),
                    ExamSubjectIn(subject_id=inactive.id),
                    ExamSubjectIn(subject_id=missing_id),
                ]
            ),
            actor=actor,
        )

    assert _error(exc) == (422, "invalid_subject")
    assert exc.value.details == {"subject_ids": sorted([str(inactive.id), str(missing_id)])}
    assert _count(db_session, "exam_subjects", exam.id) == 1


# --- BACKEND-459 get_score_grid ---


def test_score_grid_students(db_session: Session) -> None:
    class_a = make_class(db_session, name="A班", grade_levels=(3,))
    exam = _exam_with_subjects(db_session, "國語", grade_level=None, class_=class_a)
    ming = make_student(db_session, name="王小明", student_no="G-002", class_=class_a)
    an = make_student(db_session, name="林小安", student_no="G-001", class_=class_a)
    mei = make_student(db_session, name="張小美", student_no="G-003", class_=class_a)
    make_exam_score(db_session, exam, mei, _subject(db_session, "國語"), score=Decimal("70"))
    mei.status = "withdrawn"
    mei.withdrawn_on = date(2026, 10, 1)
    db_session.flush()

    grid = get_score_grid(db_session, exam.id)

    assert [(s.name, s.in_roster) for s in grid.students] == [
        ("林小安", True),
        ("王小明", True),
        ("張小美", False),
    ]
    assert {s.id for s in grid.students} == {ming.id, an.id, mei.id}
    assert grid.students[0].class_name == "A班"
    assert grid.exam.id == exam.id
    assert grid.exam.roster_count == 2
    assert [s.subject_name for s in grid.subjects] == ["國語"]


def test_score_grid_sorted_by_class_then_student_no(db_session: Session) -> None:
    class_b = make_class(db_session, name="乙班", grade_levels=(5,))
    class_a = make_class(db_session, name="甲班", grade_levels=(5,))
    exam = make_exam(db_session, grade_level=5)
    make_student(db_session, name="王小明", student_no="Q-001", grade_level=5, class_=class_b)
    make_student(db_session, name="陳小華", student_no="Q-002", grade_level=5, class_=class_a)
    make_student(db_session, name="林小安", student_no="Q-000", grade_level=5)  # 未分班排最後

    grid = get_score_grid(db_session, exam.id)

    mine = [s.name for s in grid.students if s.name in {"王小明", "陳小華", "林小安"}]
    assert mine == ["王小明", "陳小華", "林小安"]


def test_score_grid_cells(db_session: Session) -> None:
    class_a = make_class(db_session, grade_levels=(3,))
    exam = _exam_with_subjects(db_session, "國語", "數學", grade_level=None, class_=class_a)
    chinese, math = _subject(db_session, "國語"), _subject(db_session, "數學")
    ming = make_student(db_session, name="王小明", class_=class_a)
    make_student(db_session, name="陳小華", class_=class_a)  # 未登分：沒有 cell
    make_exam_score(db_session, exam, ming, chinese, score=Decimal("95"))
    make_exam_score(db_session, exam, ming, math, score=None, is_absent=True)

    grid = get_score_grid(db_session, exam.id)

    cells = {(c.student_id, c.subject_id): (c.score, c.is_absent, c.note) for c in grid.cells}
    assert cells == {
        (ming.id, chinese.id): (Decimal("95"), False, None),
        (ming.id, math.id): (None, True, None),
    }
    assert grid.model_dump(mode="json")["cells"][0]["score"] in (95.0, None)


def test_score_grid_not_found(db_session: Session) -> None:
    with pytest.raises(AppError) as exc:
        get_score_grid(db_session, uuid4())
    assert _error(exc) == (404, "exam_not_found")


def test_score_grid_query_count(db_session: Session) -> None:
    class_a = make_class(db_session, grade_levels=(3,))
    director = make_staff(db_session, display_name="陳主任")
    exam = _exam_with_subjects(
        db_session, "國語", "數學", "英語", grade_level=None, class_=class_a, status="published"
    )
    exam.published_by = director.id
    subjects = [_subject(db_session, n) for n in ("國語", "數學", "英語")]
    for index in range(40):
        student = make_student(db_session, class_=class_a)
        for subject in subjects:
            make_exam_score(db_session, exam, student, subject, score=Decimal(60 + index % 40))
    db_session.flush()
    # 模擬新的請求：identity map 清空，所有資料都要重新查詢
    db_session.expunge_all()
    statements: list[str] = []

    def record(_conn: object, _cur: object, statement: str, *_args: object) -> None:
        statements.append(statement)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", record)
    try:
        grid = get_score_grid(db_session, exam.id)
    finally:
        event.remove(engine, "before_cursor_execute", record)

    assert (len(grid.students), len(grid.cells)) == (40, 120)
    assert grid.exam.published_by_name == "陳主任"
    assert len(statements) <= 5


# --- BACKEND-462 unpublish_exam ---


def test_unpublish_exam_success(db_session: Session, actor: CurrentStaff) -> None:
    director = make_staff(db_session, display_name="陳主任")
    exam = make_exam(db_session, status="published")
    exam.published_by = director.id
    db_session.flush()
    clock = FakeClock(datetime(2026, 10, 20, 2, 0, tzinfo=UTC))

    out = unpublish_exam(db_session, exam.id, actor=actor, meta=_META, clock=clock)

    assert (out.status, out.published_at, out.published_by_name) == ("draft", None, None)
    row = db_session.get(Exam, exam.id)
    assert row is not None
    assert (row.status, row.published_at, row.published_by) == ("draft", None, None)
    [log] = db_session.execute(
        select(AuditLog).where(
            AuditLog.action == "exam.unpublish", AuditLog.entity_id == str(exam.id)
        )
    ).scalars()
    assert (log.actor_id, log.entity_type) == (actor.id, "exam")
    assert log.before == {"status": "published", "published_at": ARCHIVED_AT.isoformat()}
    assert log.after == {"status": "draft"}


def test_unpublish_exam_draft(db_session: Session, actor: CurrentStaff) -> None:
    exam = make_exam(db_session)
    clock = FakeClock(datetime(2026, 10, 20, 2, 0, tzinfo=UTC))

    with pytest.raises(AppError) as draft:
        unpublish_exam(db_session, exam.id, actor=actor, meta=_META, clock=clock)
    with pytest.raises(AppError) as missing:
        unpublish_exam(db_session, uuid4(), actor=actor, meta=_META, clock=clock)

    assert _error(draft) == (409, "exam_not_published")
    assert _error(missing) == (404, "exam_not_found")


# --- BACKEND-463 get_exam_summary ---


def test_exam_summary_stats(db_session: Session) -> None:
    class_a = make_class(db_session, grade_levels=(3,))
    exam = _exam_with_subjects(db_session, "數學", "英語", grade_level=None, class_=class_a)
    math = _subject(db_session, "數學")
    students = [make_student(db_session, class_=class_a) for _ in range(4)]
    make_exam_score(db_session, exam, students[0], math, score=Decimal("90"))
    make_exam_score(db_session, exam, students[1], math, score=Decimal("80"))
    make_exam_score(db_session, exam, students[2], math, score=None, is_absent=True)
    # students[3] 未登分

    summary = get_exam_summary(db_session, exam.id)

    assert (summary.exam_id, summary.roster_count) == (exam.id, 4)
    math_row, english_row = summary.subjects
    assert (math_row.subject_name, math_row.full_score) == ("數學", Decimal("100"))
    assert (math_row.scored_count, math_row.absent_count, math_row.missing_count) == (2, 1, 1)
    assert math_row.average == Decimal("85.00")
    assert (math_row.max, math_row.min) == (Decimal("90"), Decimal("80"))
    assert english_row.subject_name == "英語"


def test_exam_summary_rounding(db_session: Session) -> None:
    class_a = make_class(db_session, grade_levels=(3,))
    exam = _exam_with_subjects(db_session, "數學", grade_level=None, class_=class_a)
    math = _subject(db_session, "數學")
    for score in ("90", "80", "81"):
        make_exam_score(
            db_session, exam, make_student(db_session, class_=class_a), math, score=Decimal(score)
        )

    [row] = get_exam_summary(db_session, exam.id).subjects

    assert row.average == Decimal("83.67")
    assert row.missing_count == 0


def test_exam_summary_empty_subject(db_session: Session) -> None:
    class_a = make_class(db_session, grade_levels=(3,))
    exam = _exam_with_subjects(db_session, "英語", grade_level=None, class_=class_a)
    make_student(db_session, class_=class_a)
    make_student(db_session, class_=class_a)

    [row] = get_exam_summary(db_session, exam.id).subjects

    assert (row.scored_count, row.absent_count, row.missing_count) == (0, 0, 2)
    assert (row.average, row.max, row.min) == (None, None, None)
