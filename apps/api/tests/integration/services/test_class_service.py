"""BACKEND-135：app/services/class_service.py（list_classes）。"""

from datetime import date
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import CurrentStaff
from app.core.errors import AppError
from app.models.classes import SchoolClass
from app.schemas.classes import ClassCreateIn, ClassListQuery, ClassOut
from app.services.class_service import create_class, get_class, list_classes
from tests.support.factories import make_class, make_class_staff, make_staff, make_student


def _actor(staff_id: UUID) -> CurrentStaff:
    return CurrentStaff(
        id=staff_id,
        username="teacher",
        display_name="林老師",
        role_id=uuid4(),
        role_code="tutor",
        role_name="導師",
        permissions=frozenset(),
        must_change_password=False,
        token_version=0,
    )


def _mine(rows: list[ClassOut], ids: set[UUID]) -> list[ClassOut]:
    """只看本測試建立的班，避免 DB 內其他資料干擾。"""
    return [r for r in rows if r.id in ids]


def test_list_classes_archived_filter(db_session: Session) -> None:
    a = make_class(db_session, name="A班")
    b = make_class(db_session, name="B班", archived=True)
    actor = _actor(make_staff(db_session).id)
    ids = {a.id, b.id}

    default = _mine(list_classes(db_session, ClassListQuery(), actor=actor), ids)
    with_archived = _mine(
        list_classes(db_session, ClassListQuery(include_archived=True), actor=actor), ids
    )

    assert {c.name for c in default} == {"A班"}
    assert {c.name for c in with_archived} == {"A班", "B班"}
    assert next(c for c in with_archived if c.name == "B班").archived_at is not None


def test_list_classes_academic_year_filter(db_session: Session) -> None:
    old = make_class(db_session, academic_year=114)
    new = make_class(db_session, academic_year=115)
    actor = _actor(make_staff(db_session).id)

    rows = list_classes(db_session, ClassListQuery(academic_year=114), actor=actor)

    assert old.id in {r.id for r in rows}
    assert new.id not in {r.id for r in rows}
    assert {r.academic_year for r in rows} == {114}


def test_list_classes_mine(db_session: Session) -> None:
    teacher = make_staff(db_session)
    a = make_class(db_session, name="A班")
    make_class(db_session, name="C班")
    make_class_staff(db_session, a, teacher, role="lead")
    other = make_class(db_session, name="D班")
    make_class_staff(db_session, other, make_staff(db_session), role="lead")

    rows = list_classes(db_session, ClassListQuery(mine=True), actor=_actor(teacher.id))

    assert [r.id for r in rows] == [a.id]
    everyone = list_classes(db_session, ClassListQuery(), actor=_actor(teacher.id))
    assert {a.id, other.id} <= {r.id for r in everyone}


def test_list_classes_counts_and_staff(db_session: Session) -> None:
    klass = make_class(db_session, name="A班")
    empty = make_class(db_session, name="空班")
    for _ in range(2):
        make_student(db_session, class_=klass)
    withdrawn = make_student(db_session, class_=klass)
    withdrawn.status = "withdrawn"
    withdrawn.withdrawn_on = date(2026, 9, 1)
    make_student(db_session, class_=klass, archived=True)
    lead = make_staff(db_session, display_name="林老師")
    inactive = make_staff(db_session, display_name="陳老師", is_active=False)
    make_class_staff(db_session, klass, lead, role="lead")
    make_class_staff(db_session, klass, inactive, role="assistant")
    db_session.flush()

    rows = {r.id: r for r in list_classes(db_session, ClassListQuery(), actor=_actor(lead.id))}

    assert rows[klass.id].student_count == 2
    assert [(s.staff_user_id, s.display_name, s.role) for s in rows[klass.id].staff] == [
        (lead.id, "林老師", "lead")
    ]
    assert rows[empty.id].student_count == 0
    assert rows[empty.id].staff == []


def test_list_classes_order(db_session: Session) -> None:
    old = make_class(db_session, name="B班", academic_year=114)
    second = make_class(db_session, name="B班", academic_year=115)
    first_by_name = make_class(db_session, name="A班", academic_year=115)
    actor = _actor(make_staff(db_session).id)
    ids = {old.id, second.id, first_by_name.id}

    rows = _mine(list_classes(db_session, ClassListQuery(), actor=actor), ids)

    assert [r.id for r in rows] == [first_by_name.id, second.id, old.id]


def test_list_classes_order_sort_order_within_year(db_session: Session) -> None:
    first_by_sort = make_class(db_session, name="B班", academic_year=115)
    second_by_sort = make_class(db_session, name="A班", academic_year=115)
    first_by_sort.sort_order = 0
    second_by_sort.sort_order = 1
    other_year_low_sort = make_class(db_session, name="A班", academic_year=114)
    other_year_low_sort.sort_order = 0
    db_session.flush()
    actor = _actor(make_staff(db_session).id)
    ids = {first_by_sort.id, second_by_sort.id, other_year_low_sort.id}

    rows = _mine(list_classes(db_session, ClassListQuery(), actor=actor), ids)

    # 同學年先看 sort_order（B班 sort_order=0 在 A班 sort_order=1 前），學年仍優先於 sort_order
    assert [r.id for r in rows] == [first_by_sort.id, second_by_sort.id, other_year_low_sort.id]


def test_get_class_success(db_session: Session) -> None:
    klass = make_class(db_session, name="A班", grade_levels=(1, 2), academic_year=115)
    teacher = make_staff(db_session, display_name="林老師")
    inactive = make_staff(db_session, display_name="陳老師", is_active=False)
    make_class_staff(db_session, klass, teacher, role="lead")
    make_class_staff(db_session, klass, inactive, role="assistant")
    make_student(db_session, class_=klass)
    make_student(db_session, class_=klass, archived=True)
    db_session.flush()

    out = get_class(db_session, klass.id)

    assert isinstance(out, ClassOut)
    assert (out.id, out.name, out.grade_levels, out.academic_year) == (klass.id, "A班", [1, 2], 115)
    assert out.staff[0].display_name == "林老師"
    assert [s.display_name for s in out.staff] == ["林老師"]
    assert out.student_count == 1
    assert out.archived_at is None


def test_get_class_archived_and_missing(db_session: Session) -> None:
    archived = make_class(db_session, archived=True)

    out = get_class(db_session, archived.id)
    assert out.archived_at is not None

    with pytest.raises(AppError) as exc:
        get_class(db_session, uuid4())
    assert (exc.value.status, exc.value.code) == (404, "class_not_found")


def _count_classes(db: Session, year: int) -> int:
    return db.execute(
        select(func.count()).select_from(SchoolClass).where(SchoolClass.academic_year == year)
    ).scalar_one()


def test_create_class_success(db_session: Session) -> None:
    out = create_class(
        db_session, ClassCreateIn(name="低年級 A 班", grade_levels=[2, 1], academic_year=115)
    )

    assert out.name == "低年級 A 班"
    assert out.grade_levels == [1, 2]
    assert out.academic_year == 115
    assert out.student_count == 0
    assert out.staff == []
    assert out.sort_order == 0
    assert out.archived_at is None
    stored = db_session.execute(select(SchoolClass).where(SchoolClass.id == out.id)).scalar_one()
    assert stored.name == "低年級 A 班"


def test_create_class_conflict(db_session: Session) -> None:
    create_class(db_session, ClassCreateIn(name="低年級 A 班", grade_levels=[1], academic_year=115))
    before = _count_classes(db_session, 115)

    with pytest.raises(AppError) as exc:
        create_class(
            db_session, ClassCreateIn(name="低年級 a 班 ", grade_levels=[1], academic_year=115)
        )

    assert (exc.value.status, exc.value.code) == (409, "class_name_taken")
    assert _count_classes(db_session, 115) == before
    other_year = create_class(
        db_session, ClassCreateIn(name="低年級 a 班", grade_levels=[1], academic_year=116)
    )
    assert other_year.academic_year == 116


def test_create_class_archived_name_reusable(db_session: Session) -> None:
    make_class(db_session, name="高年級班", academic_year=115, archived=True)

    out = create_class(
        db_session, ClassCreateIn(name="高年級班", grade_levels=[5, 6], academic_year=115)
    )

    assert out.name == "高年級班"
    assert out.archived_at is None
