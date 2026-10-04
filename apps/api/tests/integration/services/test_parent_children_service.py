"""BACKEND-182：app/services/parent_children_service.py（list_children）。"""

from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.storage import StorageError
from app.schemas.parent_children import ChildDetailOut, ChildGuardianOut, ChildSummaryOut
from app.services.parent_children_service import get_child, list_children
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


def test_get_child_detail(db_session: Session) -> None:
    parent = make_parent(db_session)
    school = make_school(db_session, name="新生國民小學")
    school.short_name = "新生"
    klass = make_class(db_session, name="低年級 A 班")
    ming = make_student(db_session, name="王小明", grade_level=2, class_=klass, school=school)
    ming.school_class = "二年三班"
    ming.enrolled_on = date(2025, 9, 1)
    ming.photo_path = _photo_path(ming.id)
    mine = make_guardian(db_session, ming, parent=parent, relation="mother", is_primary=True)
    mine.receives_notifications = False
    # 同學生的其他監護人（另一位家長）不影響 my_guardian
    make_guardian(
        db_session, ming, parent=make_parent(db_session), relation="father", can_pickup=False
    )
    db_session.flush()

    out = get_child(db_session, parent.id, ming.id, storage=FakeStorage())

    assert isinstance(out, ChildDetailOut)
    assert out.my_guardian == ChildGuardianOut(
        relation="mother", is_primary=True, can_pickup=True, receives_notifications=False
    )
    assert out.name == "王小明"
    assert out.class_name == "低年級 A 班"
    assert out.school_name == "新生"
    assert out.school_class == "二年三班"
    assert out.enrolled_on == date(2025, 9, 1)
    assert out.photo_url == f"https://storage.test/student-photos/{ming.photo_path}?exp=300"
    # 家長端不輸出敏感 / 內部欄位
    assert not {"id_number", "health_note", "note"} & set(out.model_dump())


def test_get_child_idor(db_session: Session) -> None:
    parent = make_parent(db_session)
    other = make_parent(db_session)
    others_child = make_student(db_session, name="陳小華")
    make_guardian(db_session, others_child, parent=other)
    revoked = make_student(db_session, name="已解除")
    make_guardian(db_session, revoked, parent=parent, archived=True)

    for student_id in (others_child.id, revoked.id, uuid4()):
        with pytest.raises(AppError) as exc:
            get_child(db_session, parent.id, student_id, storage=FakeStorage())
        assert (exc.value.status, exc.value.code) == (404, "student_not_found")


def test_get_child_withdrawn_still_readable(db_session: Session) -> None:
    parent = make_parent(db_session)
    student = make_student(db_session)
    student.status = "withdrawn"
    student.withdrawn_on = date(2026, 9, 1)
    make_guardian(db_session, student, parent=parent)
    db_session.flush()

    out = get_child(db_session, parent.id, student.id, storage=FakeStorage())

    assert out.status == "withdrawn"
