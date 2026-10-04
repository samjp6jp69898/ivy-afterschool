"""BACKEND-168：app/services/guardian_service.py（list_for_student、to_guardian_out）。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models.parents import Guardian, ParentBindingCode
from app.schemas.guardians import GuardianCreateIn
from app.services.guardian_service import create_guardian, list_for_student, to_guardian_out
from tests.support.factories import make_guardian, make_parent, make_staff, make_student
from tests.support.fake_clock import FakeClock

_NOW = datetime(2026, 9, 10, 4, 0, tzinfo=UTC)
_BASE = datetime(2026, 9, 1, tzinfo=UTC)


def _clock() -> FakeClock:
    return FakeClock(_NOW)


def _stamp(db: Session, *guardians: Guardian) -> None:
    """同一交易內 created_at 都是 now()，明確指定以固定「建立先後」。"""
    for index, guardian in enumerate(guardians):
        guardian.created_at = _BASE + timedelta(minutes=index)
    db.flush()


def _code(
    db: Session,
    guardian: Guardian,
    staff_id: object,
    *,
    expires_at: datetime,
    used: bool = False,
) -> None:
    db.add(
        ParentBindingCode(
            guardian_id=guardian.id,
            code_hash=uuid4().hex + uuid4().hex,
            expires_at=expires_at,
            used_at=_NOW if used else None,
            created_by=staff_id,
            # CHECK：expires_at 必須晚於 created_at；過期碼的 created_at 要更早
            created_at=_NOW - timedelta(days=5),
        )
    )
    db.flush()


def test_list_guardians_order_and_archived(db_session: Session) -> None:
    student = make_student(db_session)
    first = make_guardian(db_session, student, name="爸爸", relation="father")
    primary = make_guardian(db_session, student, name="媽媽", is_primary=True)
    gone = make_guardian(db_session, student, name="奶奶", relation="grandparent", archived=True)
    later = make_guardian(db_session, student, name="阿姨", relation="other")
    _stamp(db_session, first, primary, gone, later)

    result = list_for_student(db_session, student.id, clock=_clock())

    assert [g.name for g in result] == ["媽媽", "爸爸", "阿姨"]
    assert result[0].is_primary is True
    assert {g.student_id for g in result} == {student.id}


def test_list_guardians_binding_status(db_session: Session) -> None:
    staff = make_staff(db_session)
    student = make_student(db_session)
    parent = make_parent(db_session, display_name="王媽媽")
    g1 = make_guardian(db_session, student, parent=parent, name="g1", is_primary=True)
    g2 = make_guardian(db_session, student, name="g2")
    g3 = make_guardian(db_session, student, name="g3", relation="father")
    g4 = make_guardian(db_session, student, name="g4", relation="other")
    _stamp(db_session, g1, g2, g3, g4)
    expires = _NOW + timedelta(days=3)
    _code(db_session, g2, staff.id, expires_at=_NOW + timedelta(days=1))
    _code(db_session, g2, staff.id, expires_at=expires)
    _code(db_session, g3, staff.id, expires_at=_NOW - timedelta(seconds=1))
    _code(db_session, g4, staff.id, expires_at=_NOW + timedelta(days=2), used=True)

    result = list_for_student(db_session, student.id, clock=_clock())

    assert [g.binding.status for g in result] == ["bound", "code_issued", "unbound", "unbound"]
    assert result[0].binding.parent_display_name == "王媽媽"
    assert result[0].binding.code_expires_at is None
    assert result[1].binding.code_expires_at == expires  # 最晚到期的未使用碼
    assert result[1].binding.parent_display_name is None
    assert result[2].binding.code_expires_at is None


def test_list_guardians_bound_ignores_codes(db_session: Session) -> None:
    staff = make_staff(db_session)
    student = make_student(db_session)
    guardian = make_guardian(db_session, student, parent=make_parent(db_session))
    _code(db_session, guardian, staff.id, expires_at=_NOW + timedelta(days=1))

    (only,) = list_for_student(db_session, student.id, clock=_clock())

    assert only.binding.status == "bound"
    assert only.binding.code_expires_at is None


def test_list_guardians_archived_student_visible(db_session: Session) -> None:
    student = make_student(db_session, archived=True)
    make_guardian(db_session, student)

    assert len(list_for_student(db_session, student.id, clock=_clock())) == 1


def test_list_guardians_student_not_found(db_session: Session) -> None:
    with pytest.raises(AppError) as exc:
        list_for_student(db_session, uuid4(), clock=_clock())

    assert (exc.value.status, exc.value.code) == (404, "student_not_found")


def test_list_guardians_to_guardian_out(db_session: Session) -> None:
    student = make_student(db_session)
    guardian = make_guardian(db_session, student, name="爸爸", relation="father")
    expires = _NOW + timedelta(days=1)

    unbound = to_guardian_out(guardian, None)
    issued = to_guardian_out(guardian, expires)

    assert unbound.binding.status == "unbound"
    assert issued.binding.status == "code_issued"
    assert issued.binding.code_expires_at == expires
    assert (issued.id, issued.name, issued.relation) == (guardian.id, "爸爸", "father")


def test_create_guardian_success(db_session: Session) -> None:
    student = make_student(db_session)

    out = create_guardian(
        db_session,
        student.id,
        GuardianCreateIn(name="王爸爸", relation="father", phone="0912-000-123"),
        clock=_clock(),
    )

    assert out.binding.status == "unbound"
    assert out.is_primary is False
    assert (out.name, out.relation, out.phone) == ("王爸爸", "father", "0912-000-123")
    assert (out.can_pickup, out.receives_notifications) == (True, True)
    assert out.student_id == student.id
    stored = db_session.execute(select(Guardian).where(Guardian.id == out.id)).scalar_one()
    assert stored.parent_account_id is None
    assert stored.archived_at is None


def test_create_guardian_primary_switch(db_session: Session) -> None:
    student = make_student(db_session)
    g1 = make_guardian(db_session, student, name="王媽媽", is_primary=True)
    archived_primary = make_guardian(
        db_session, student, name="已封存", relation="other", archived=True
    )

    out = create_guardian(
        db_session,
        student.id,
        GuardianCreateIn(name="王爸爸", relation="father", is_primary=True),
        clock=_clock(),
    )

    db_session.refresh(g1)
    db_session.refresh(archived_primary)
    assert g1.is_primary is False
    assert out.is_primary is True
    primaries = db_session.execute(
        select(func.count())
        .select_from(Guardian)
        .where(
            Guardian.student_id == student.id, Guardian.is_primary, Guardian.archived_at.is_(None)
        )
    ).scalar_one()
    assert primaries == 1


def test_create_guardian_non_primary_keeps_existing_primary(db_session: Session) -> None:
    student = make_student(db_session)
    g1 = make_guardian(db_session, student, name="王媽媽", is_primary=True)

    create_guardian(
        db_session, student.id, GuardianCreateIn(name="王爸爸", relation="father"), clock=_clock()
    )

    db_session.refresh(g1)
    assert g1.is_primary is True


def test_create_guardian_archived_student(db_session: Session) -> None:
    student = make_student(db_session, archived=True)

    with pytest.raises(AppError) as exc:
        create_guardian(
            db_session,
            student.id,
            GuardianCreateIn(name="王爸爸", relation="father"),
            clock=_clock(),
        )

    assert (exc.value.status, exc.value.code) == (409, "student_archived")


def test_create_guardian_student_not_found(db_session: Session) -> None:
    with pytest.raises(AppError) as exc:
        create_guardian(
            db_session, uuid4(), GuardianCreateIn(name="王爸爸", relation="father"), clock=_clock()
        )

    assert (exc.value.status, exc.value.code) == (404, "student_not_found")
