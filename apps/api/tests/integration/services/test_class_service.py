"""BACKEND-135：app/services/class_service.py（list_classes）。"""

from datetime import date
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.api.deps import CurrentStaff
from app.schemas.classes import ClassListQuery, ClassOut
from app.services.class_service import list_classes
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
