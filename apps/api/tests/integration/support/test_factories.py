"""BACKEND-022：tests/support/factories.py（ORM 測資 factory，經 app_backend 寫入）。"""

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.permissions import resolve_effective_permissions
from app.core.security.passwords import verify_password
from app.models.account import StaffUser
from app.models.classes import ClassStaff, SchoolClass
from app.models.parents import Guardian, ParentAccount
from app.models.reference import School
from app.models.students import Student
from tests.support.factories import (
    make_class,
    make_class_staff,
    make_guardian,
    make_parent,
    make_role,
    make_school,
    make_staff,
    make_student,
)


def test_factories_make_staff_permissions(db_session: Session) -> None:
    staff = make_staff(db_session, permissions=["students:read", "pickup:operate"])

    effective = resolve_effective_permissions(
        staff.role.permissions, staff.extra_permissions, staff.revoked_permissions
    )
    assert effective == frozenset({"students:read", "pickup:operate"})
    assert verify_password("Passw0rd-Test1", staff.password_hash) is True
    assert staff.role.is_system is False

    # 查回 DB 的列與預設值一致
    db_session.expunge_all()
    row = db_session.execute(select(StaffUser).where(StaffUser.id == staff.id)).scalar_one()
    assert row.display_name == "林老師"
    assert row.is_active is True
    assert row.must_change_password is False
    assert sorted(row.role.permissions) == ["pickup:operate", "students:read"]

    # extra / revoked 也反映在有效權限上
    staff2 = make_staff(
        db_session,
        permissions=["students:read", "students:write"],
        extra_permissions=["exams:read"],
        revoked_permissions=["students:write"],
    )
    assert resolve_effective_permissions(
        staff2.role.permissions, staff2.extra_permissions, staff2.revoked_permissions
    ) == frozenset({"students:read", "exams:read"})

    # 兩者皆無 → 空權限角色
    nobody = make_staff(db_session)
    assert nobody.role.permissions == []


def test_factories_make_staff_system_role(db_session: Session) -> None:
    staff = make_staff(db_session, role_code="tutor")

    assert staff.role.code == "tutor"
    assert staff.role.is_system is True
    assert "homework:write" in staff.role.permissions

    admin = make_staff(db_session, role_code="admin")
    assert admin.role.code == "admin"
    assert admin.role.permissions == ["*"]


def test_factories_unique_defaults(db_session: Session) -> None:
    s1 = make_student(db_session)
    s2 = make_student(db_session)
    assert s1.student_no != s2.student_no
    assert s1.name == s2.name == "王小明"
    assert s1.grade_level == 3
    assert s1.status == "active"
    assert s1.archived_at is None

    a = make_staff(db_session)
    b = make_staff(db_session)
    assert a.username != b.username

    c1 = make_class(db_session)
    c2 = make_class(db_session)
    assert c1.name != c2.name
    assert c1.grade_levels == [1, 2]
    assert c1.academic_year == 115

    k1 = make_school(db_session)
    k2 = make_school(db_session)
    assert k1.name != k2.name

    r1 = make_role(db_session)
    r2 = make_role(db_session)
    assert r1.code != r2.code

    db_session.flush()
    db_session.expunge_all()
    rows = db_session.execute(
        select(Student.student_no).where(Student.id.in_([s1.id, s2.id]))
    ).scalars()
    assert set(rows) == {s1.student_no, s2.student_no}


def test_factories_guardian_binding(db_session: Session) -> None:
    p = make_parent(db_session)
    student = make_student(db_session)
    g = make_guardian(db_session, student, parent=p, is_primary=True)

    assert g.parent_account_id == p.id
    assert g.student_id == student.id
    assert g.is_primary is True
    assert g.can_pickup is True
    assert g.receives_notifications is True
    assert g.relation == "mother"
    assert g.name == "王媽媽"
    assert re.fullmatch(r"U[0-9a-f]{32}", p.line_user_id)
    assert p.status == "active"
    assert p.display_name == "王媽媽"

    # 未給 parent 的監護人尚未綁定
    g2 = make_guardian(db_session, student, name="王爸爸", relation="father")
    assert g2.parent_account_id is None

    db_session.expunge_all()
    row = db_session.execute(select(Guardian).where(Guardian.id == g.id)).scalar_one()
    assert row.parent_account_id == p.id
    parent_row = db_session.execute(
        select(ParentAccount).where(ParentAccount.id == p.id)
    ).scalar_one()
    assert parent_row.line_user_id == p.line_user_id


def test_factories_class_student_school_links(db_session: Session) -> None:
    school = make_school(db_session, name="新生國小")
    klass = make_class(db_session, name="低年級 B 班", grade_levels=[1], archived=True)
    staff = make_staff(db_session, role_code="tutor")
    link = make_class_staff(db_session, klass, staff)
    student = make_student(
        db_session, student_no="S-2026-01", class_=klass, school=school, archived=True
    )

    assert link.role == "lead"
    assert link.class_id == klass.id
    assert link.staff_user_id == staff.id
    assert klass.archived_at is not None
    assert student.archived_at is not None
    assert student.class_id == klass.id
    assert student.school_id == school.id

    db_session.expunge_all()
    stored_class = db_session.execute(
        select(SchoolClass).where(SchoolClass.id == klass.id)
    ).scalar_one()
    assert stored_class.name == "低年級 B 班"
    assert stored_class.grade_levels == [1]
    assert db_session.execute(select(School.name).where(School.id == school.id)).scalar_one() == (
        "新生國小"
    )
    assert (
        db_session.execute(select(ClassStaff.role).where(ClassStaff.id == link.id)).scalar_one()
        == "lead"
    )
    stored_student = db_session.execute(
        select(Student).where(Student.id == student.id)
    ).scalar_one()
    assert stored_student.student_no == "S-2026-01"
    assert stored_student.class_ is not None
    assert stored_student.class_.id == klass.id
