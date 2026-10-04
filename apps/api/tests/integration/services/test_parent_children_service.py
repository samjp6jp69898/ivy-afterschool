"""BACKEND-182：app/services/parent_children_service.py（list_children）。"""

from uuid import uuid4

from sqlalchemy.orm import Session

from app.core.storage import StorageError
from app.schemas.parent_children import ChildSummaryOut
from app.services.parent_children_service import list_children
from app.services.parent_scope import get_parent_student_ids
from tests.support.factories import (
    make_class,
    make_guardian,
    make_parent,
    make_school,
    make_student,
)
from tests.support.fake_storage import FakeStorage


def _photo_path(owner: object) -> str:
    return f"{owner}/{uuid4().hex}.jpg"


def test_list_children_fields(db_session: Session) -> None:
    p = make_parent(db_session)
    school = make_school(db_session, name="新生國民小學")
    school.short_name = "新生"
    klass = make_class(db_session, name="低年級 A 班")
    ming = make_student(db_session, name="王小明", grade_level=2, class_=klass, school=school)
    ming.photo_path = _photo_path(ming.id)
    make_guardian(db_session, ming, parent=p)
    db_session.flush()

    result = list_children(db_session, p.id, storage=FakeStorage())

    assert result == [
        ChildSummaryOut(
            id=ming.id,
            name="王小明",
            grade_level=2,
            class_name="低年級 A 班",
            school_name="新生",
            photo_url=f"https://storage.test/student-photos/{ming.photo_path}?exp=300",
            status="active",
        )
    ]


def test_list_children_school_name_falls_back_and_nulls(db_session: Session) -> None:
    p = make_parent(db_session)
    school = make_school(db_session, name="仁愛國小")
    with_school = make_student(db_session, name="王小明", school=school)
    bare = make_student(db_session, name="陳小華", status="suspended")
    make_guardian(db_session, with_school, parent=p)
    make_guardian(db_session, bare, parent=p)

    by_name = {c.name: c for c in list_children(db_session, p.id, storage=FakeStorage())}

    assert by_name["王小明"].school_name == "仁愛國小"
    assert by_name["王小明"].class_name is None
    assert by_name["王小明"].photo_url is None
    assert by_name["陳小華"].school_name is None
    assert by_name["陳小華"].status == "suspended"


def test_list_children_scope(db_session: Session) -> None:
    p = make_parent(db_session)
    other = make_parent(db_session)
    visible = make_student(db_session, name="王小明")
    guardian_archived = make_student(db_session, name="陳小華")
    student_archived = make_student(db_session, name="林小安", archived=True)
    others_child = make_student(db_session, name="張小芳")
    make_guardian(db_session, visible, parent=p)
    make_guardian(db_session, guardian_archived, parent=p, archived=True)
    make_guardian(db_session, student_archived, parent=p)
    make_guardian(db_session, others_child, parent=other)

    result = list_children(db_session, p.id, storage=FakeStorage())

    assert [c.id for c in result] == [visible.id]


def test_list_children_order_matches_parent_scope(db_session: Session) -> None:
    p = make_parent(db_session)
    students = [make_student(db_session, name=n) for n in ("陳小華", "王小明", "林小安")]
    for s in students:
        make_guardian(db_session, s, parent=p)

    result = list_children(db_session, p.id, storage=FakeStorage())

    assert [c.id for c in result] == get_parent_student_ids(db_session, p.id)
    assert len(result) == 3


def test_list_children_photo_sign_failure_returns_none(db_session: Session) -> None:
    p = make_parent(db_session)
    student = make_student(db_session)
    student.photo_path = _photo_path(student.id)
    make_guardian(db_session, student, parent=p)
    db_session.flush()
    storage = FakeStorage()
    storage.sign_error = StorageError("S3 sign 失敗")

    result = list_children(db_session, p.id, storage=storage)

    assert len(result) == 1
    assert result[0].photo_url is None


def test_list_children_empty(db_session: Session) -> None:
    assert list_children(db_session, make_parent(db_session).id, storage=FakeStorage()) == []
