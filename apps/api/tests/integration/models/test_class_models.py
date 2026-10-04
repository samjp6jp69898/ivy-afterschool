"""BACKEND-130：app/models/classes.py（SchoolClass、ClassStaff）。"""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.account import Role, StaffUser
from app.models.classes import ClassStaff, SchoolClass

_ARGON2ID_HASH = "$argon2id$v=19$m=65536,t=3,p=4$abc$def"


def _make_staff(db_session: Session) -> StaffUser:
    suffix = uuid4().hex[:8]
    role = Role(code=f"probe_{suffix}", name="測試", permissions=[])
    staff = StaffUser(
        username=f"teacher.{suffix}",
        password_hash=_ARGON2ID_HASH,
        display_name="林老師",
        role=role,
    )
    db_session.add_all([role, staff])
    db_session.flush()
    return staff


def _make_class(db_session: Session) -> SchoolClass:
    school_class = SchoolClass(name="低年級 A 班", grade_levels=[1, 2], academic_year=115)
    db_session.add(school_class)
    db_session.flush()
    return school_class


def test_class_models_roundtrip(db_session: Session) -> None:
    school_class = _make_class(db_session)
    db_session.refresh(school_class)

    assert isinstance(school_class.id, UUID)
    assert school_class.grade_levels == [1, 2]
    assert all(isinstance(g, int) for g in school_class.grade_levels)
    assert school_class.sort_order == 0
    assert school_class.archived_at is None
    assert school_class.is_archived is False
    assert school_class.academic_year == 115

    db_session.expunge_all()
    loaded = db_session.execute(
        select(SchoolClass).where(SchoolClass.id == school_class.id)
    ).scalar_one()
    assert loaded.grade_levels == [1, 2]
    assert loaded.staff_links == []


def test_class_models_staff_cascade(db_session: Session) -> None:
    staff = _make_staff(db_session)
    school_class = _make_class(db_session)
    link = ClassStaff(class_id=school_class.id, staff_user_id=staff.id, role="lead")
    db_session.add(link)
    db_session.flush()
    link_id = link.id
    db_session.expunge_all()

    loaded = db_session.execute(
        select(SchoolClass).where(SchoolClass.id == school_class.id)
    ).scalar_one()
    assert SchoolClass.staff_links.property.lazy == "selectin"
    assert ClassStaff.staff.property.lazy == "joined"
    assert "staff_links" not in inspect(loaded).unloaded
    assert [link.role for link in loaded.staff_links] == ["lead"]
    assert "staff" not in inspect(loaded.staff_links[0]).unloaded
    assert loaded.staff_links[0].staff.username == staff.username

    db_session.delete(loaded)
    db_session.flush()

    assert (
        db_session.execute(select(ClassStaff).where(ClassStaff.id == link_id)).scalar_one_or_none()
        is None
    )


def test_class_models_role_check(db_session: Session) -> None:
    staff = _make_staff(db_session)
    school_class = _make_class(db_session)

    db_session.add(ClassStaff(class_id=school_class.id, staff_user_id=staff.id, role="owner"))
    with pytest.raises(IntegrityError) as excinfo:
        db_session.flush()
    assert getattr(excinfo.value.orig, "sqlstate", None) == "23514"
    # savepoint 模式的 rollback 會連同本測試先前建立的資料一起退回，重建後再驗 server default
    db_session.rollback()
    staff = _make_staff(db_session)
    school_class = _make_class(db_session)

    # 預設角色 assistant（server default）
    link = ClassStaff(class_id=school_class.id, staff_user_id=staff.id)
    db_session.add(link)
    db_session.flush()
    db_session.refresh(link)
    assert link.role == "assistant"
