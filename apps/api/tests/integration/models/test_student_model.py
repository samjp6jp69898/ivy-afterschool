"""BACKEND-131：app/models/students.py（Student）。

guardians 關係（lazy='raise'）需要 Guardian model，由 BACKEND-132 的
test_parent_models_student_guardians_raise 驗收。
"""

from datetime import date
from uuid import UUID

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.classes import SchoolClass
from app.models.reference import School
from app.models.students import Student

_HMAC = "a" * 64


def test_student_model_roundtrip(db_session: Session) -> None:
    student = Student(
        student_no="S115001",
        name="王小明",
        grade_level=3,
        id_number_enc=b"\x01abc",
        id_number_hmac=_HMAC,
        health_note_enc=b"\x01\x00\xff",
        birthday=date(2017, 5, 1),
    )
    db_session.add(student)
    db_session.flush()
    db_session.refresh(student)

    assert isinstance(student.id, UUID)
    assert student.status == "active"
    assert student.archived_at is None
    assert student.is_archived is False
    assert isinstance(student.id_number_enc, bytes)
    assert student.id_number_enc == b"\x01abc"
    assert isinstance(student.health_note_enc, bytes)
    assert student.health_note_enc == b"\x01\x00\xff"
    assert student.birthday == date(2017, 5, 1)

    db_session.expunge_all()
    loaded = db_session.execute(select(Student).where(Student.id == student.id)).scalar_one()
    assert (loaded.student_no, loaded.name, loaded.grade_level) == ("S115001", "王小明", 3)
    assert loaded.id_number_enc == b"\x01abc"
    assert loaded.id_number_hmac == _HMAC
    assert loaded.school is None
    assert loaded.class_ is None
    assert loaded.school_class is None


def test_student_model_pair_check(db_session: Session) -> None:
    db_session.add(
        Student(student_no="S115002", name="林小華", grade_level=2, id_number_enc=b"\x01abc")
    )
    with pytest.raises(IntegrityError) as excinfo:
        db_session.flush()
    assert getattr(excinfo.value.orig, "sqlstate", None) == "23514"
    diag = getattr(excinfo.value.orig, "diag", None)
    assert getattr(diag, "constraint_name", None) == "ck_students_id_number_pair"


def test_student_model_relationships_joined(db_session: Session) -> None:
    school = School(name="臺北市大安區新生國小（學生探針）", short_name="新生")
    school_class = SchoolClass(name="中年級 B 班", grade_levels=[3, 4], academic_year=115)
    db_session.add_all([school, school_class])
    db_session.flush()
    student = Student(
        student_no="S115003",
        name="陳小美",
        grade_level=3,
        school_id=school.id,
        school_class="3年2班",
        class_id=school_class.id,
    )
    db_session.add(student)
    db_session.flush()
    db_session.expunge_all()

    loaded = db_session.execute(select(Student).where(Student.id == student.id)).scalar_one()
    assert Student.school.property.lazy == "joined"
    assert Student.class_.property.lazy == "joined"
    state = inspect(loaded)
    assert "school" not in state.unloaded
    assert "class_" not in state.unloaded
    assert loaded.school is not None
    assert loaded.school.short_name == "新生"
    assert loaded.class_ is not None
    assert loaded.class_.name == "中年級 B 班"
    # school_class 是就讀國小的班級文字，與安親班班級 class_ 不同
    assert loaded.school_class == "3年2班"
