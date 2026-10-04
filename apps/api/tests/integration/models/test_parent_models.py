"""BACKEND-132：app/models/parents.py（ParentAccount、Guardian、ParentBindingCode）
與 Student.guardians。
"""

from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, inspect, select
from sqlalchemy.exc import IntegrityError, InvalidRequestError
from sqlalchemy.orm import Session, selectinload

from app.models.account import Role, StaffUser
from app.models.parents import Guardian, ParentAccount, ParentBindingCode
from app.models.students import Student

_ARGON2ID_HASH = "$argon2id$v=19$m=65536,t=3,p=4$abc$def"


def _make_student(db_session: Session) -> Student:
    student = Student(student_no=f"S{uuid4().hex[:8]}", name="王小明", grade_level=3)
    db_session.add(student)
    db_session.flush()
    return student


def _make_staff(db_session: Session) -> StaffUser:
    suffix = uuid4().hex[:8]
    role = Role(code=f"probe_{suffix}", name="測試", permissions=[])
    staff = StaffUser(
        username=f"clerk.{suffix}",
        password_hash=_ARGON2ID_HASH,
        display_name="林行政",
        role=role,
    )
    db_session.add_all([role, staff])
    db_session.flush()
    return staff


def test_parent_models_defaults(db_session: Session) -> None:
    parent = ParentAccount(line_user_id="U" + "a" * 32, display_name="王媽媽")
    student = _make_student(db_session)
    db_session.add(parent)
    db_session.flush()
    guardian = Guardian(
        student_id=student.id,
        parent_account_id=parent.id,
        name="王媽媽",
        relation="mother",
        phone="0912-000-123",
    )
    db_session.add(guardian)
    db_session.flush()
    db_session.refresh(parent)
    db_session.refresh(guardian)

    assert isinstance(parent.id, UUID)
    assert parent.status == "active"
    assert parent.token_version == 0
    assert parent.last_login_at is None
    assert guardian.can_pickup is True
    assert guardian.receives_notifications is True
    assert guardian.is_primary is False
    assert guardian.archived_at is None
    assert guardian.is_archived is False

    db_session.expunge_all()
    loaded = db_session.execute(select(Guardian).where(Guardian.id == guardian.id)).scalar_one()
    assert Guardian.student.property.lazy == "joined"
    assert Guardian.parent_account.property.lazy == "joined"
    state = inspect(loaded)
    assert "student" not in state.unloaded
    assert "parent_account" not in state.unloaded
    assert loaded.student.name == "王小明"
    assert loaded.parent_account is not None
    assert loaded.parent_account.line_user_id == "U" + "a" * 32


def test_parent_models_one_primary(db_session: Session) -> None:
    student = _make_student(db_session)
    db_session.add(
        Guardian(student_id=student.id, name="王爸爸", relation="father", is_primary=True)
    )
    db_session.flush()

    db_session.add(
        Guardian(student_id=student.id, name="王媽媽", relation="mother", is_primary=True)
    )
    with pytest.raises(IntegrityError) as excinfo:
        db_session.flush()
    assert getattr(excinfo.value.orig, "sqlstate", None) == "23505"
    diag = getattr(excinfo.value.orig, "diag", None)
    assert getattr(diag, "constraint_name", None) == "uq_guardians_one_primary"


def test_parent_models_binding_code(db_session: Session) -> None:
    staff = _make_staff(db_session)
    guardian = Guardian(student_id=_make_student(db_session).id, name="王媽媽", relation="mother")
    db_session.add(guardian)
    db_session.flush()
    # DB CHECK expires_at > created_at 以 DB now() 比較，到期時間也取 DB 時間
    db_now = db_session.execute(select(func.now())).scalar_one()
    code = ParentBindingCode(
        guardian_id=guardian.id,
        code_hash="b" * 64,
        expires_at=db_now + timedelta(days=7),
        created_by=staff.id,
    )
    db_session.add(code)
    db_session.flush()
    db_session.expunge_all()

    loaded = db_session.execute(
        select(ParentBindingCode).where(ParentBindingCode.id == code.id)
    ).scalar_one()
    assert loaded.used_at is None
    assert loaded.code_hash == "b" * 64
    assert loaded.guardian_id == guardian.id
    assert loaded.created_by == staff.id
    assert loaded.expires_at == db_now + timedelta(days=7)


def test_parent_models_student_guardians_raise(db_session: Session) -> None:
    student = _make_student(db_session)
    db_session.add(Guardian(student_id=student.id, name="王媽媽", relation="mother"))
    db_session.flush()
    db_session.expunge_all()

    plain = db_session.execute(select(Student).where(Student.id == student.id)).scalar_one()
    assert Student.guardians.property.lazy == "raise"
    with pytest.raises(InvalidRequestError):
        _ = plain.guardians
    db_session.expunge_all()

    loaded = db_session.execute(
        select(Student).options(selectinload(Student.guardians)).where(Student.id == student.id)
    ).scalar_one()
    assert [g.name for g in loaded.guardians] == ["王媽媽"]
    assert loaded.guardians[0].student is loaded
