"""BACKEND-168：app/services/guardian_service.py（list_for_student、to_guardian_out）。
BACKEND-170：update_guardian（部分更新、主要聯絡人互斥切換、封存 / 不存在 404）。
BACKEND-171：archive_guardian（軟刪除、作廢未使用綁定碼、已綁定寫 guardian.unbind 稽核）。
BACKEND-172：unbind_guardian（解除綁定、未綁定 409、不影響家長的其他綁定）。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import CurrentStaff
from app.core.errors import AppError
from app.core.request_meta import RequestMeta
from app.models.audit import AuditLog
from app.models.parents import Guardian, ParentBindingCode
from app.schemas.guardians import GuardianCreateIn, GuardianUpdateIn
from app.services.guardian_service import (
    archive_guardian,
    create_guardian,
    list_for_student,
    to_guardian_out,
    unbind_guardian,
    update_guardian,
)
from app.services.parent_scope import get_parent_student_ids
from tests.support.factories import make_guardian, make_parent, make_staff, make_student
from tests.support.fake_clock import FakeClock

_NOW = datetime(2026, 9, 10, 4, 0, tzinfo=UTC)
_BASE = datetime(2026, 9, 1, tzinfo=UTC)
_META = RequestMeta(ip="127.0.0.1", user_agent="pytest", request_id="req-1")


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


# --- BACKEND-170：update_guardian ----------------------------------------------------------------


def _actor() -> CurrentStaff:
    return CurrentStaff(
        id=uuid4(),
        username="clerk",
        display_name="陳行政",
        role_id=uuid4(),
        role_code="clerk",
        role_name="行政",
        permissions=frozenset({"students:write"}),
        must_change_password=False,
        token_version=0,
    )


def _unused_codes(db: Session, guardian_id: object) -> int:
    return db.execute(
        select(func.count())
        .select_from(ParentBindingCode)
        .where(ParentBindingCode.guardian_id == guardian_id, ParentBindingCode.used_at.is_(None))
    ).scalar_one()


def _unbind_audits(db: Session, guardian_id: object) -> list[AuditLog]:
    return list(
        db.execute(
            select(AuditLog).where(
                AuditLog.action == "guardian.unbind", AuditLog.entity_id == str(guardian_id)
            )
        ).scalars()
    )


def test_update_guardian_partial(db_session: Session) -> None:
    staff = make_staff(db_session)
    student = make_student(db_session)
    guardian = make_guardian(db_session, student, name="王媽媽", is_primary=True, can_pickup=False)
    guardian.phone = "0912-000-001"
    db_session.flush()
    expires = _NOW + timedelta(days=2)
    _code(db_session, guardian, staff.id, expires_at=expires)

    out = update_guardian(
        db_session, guardian.id, GuardianUpdateIn(phone="0912-000-002"), clock=_clock()
    )

    assert out.id == guardian.id
    assert out.phone == "0912-000-002"
    assert (out.name, out.relation, out.is_primary, out.can_pickup) == (
        "王媽媽",
        "mother",
        True,
        False,
    )
    assert out.receives_notifications is True
    assert out.binding.status == "code_issued"
    assert out.binding.code_expires_at == expires
    db_session.refresh(guardian)
    assert (guardian.phone, guardian.name, guardian.is_primary) == ("0912-000-002", "王媽媽", True)
    # 清除電話（nullable）
    cleared = update_guardian(
        db_session, guardian.id, GuardianUpdateIn.model_validate({"phone": None}), clock=_clock()
    )
    assert cleared.phone is None


def test_update_guardian_primary_switch(db_session: Session) -> None:
    student = make_student(db_session)
    g1 = make_guardian(db_session, student, name="王媽媽", is_primary=True)
    g2 = make_guardian(db_session, student, name="王爸爸", relation="father")
    other_student_primary = make_guardian(db_session, make_student(db_session), is_primary=True)

    out = update_guardian(db_session, g2.id, GuardianUpdateIn(is_primary=True), clock=_clock())

    db_session.refresh(g1)
    db_session.refresh(g2)
    db_session.refresh(other_student_primary)
    assert out.is_primary is True
    assert (g1.is_primary, g2.is_primary) == (False, True)
    assert other_student_primary.is_primary is True  # 別的學生不受影響
    # 允許學生沒有主要聯絡人
    update_guardian(db_session, g2.id, GuardianUpdateIn(is_primary=False), clock=_clock())
    db_session.refresh(g1)
    db_session.refresh(g2)
    assert (g1.is_primary, g2.is_primary) == (False, False)
    # 自己已是 primary 再設 True 不會撞 uq_guardians_one_primary
    update_guardian(db_session, g1.id, GuardianUpdateIn(is_primary=True), clock=_clock())
    again = update_guardian(db_session, g1.id, GuardianUpdateIn(is_primary=True), clock=_clock())
    assert again.is_primary is True


def test_update_guardian_not_found(db_session: Session) -> None:
    student = make_student(db_session)
    archived = make_guardian(db_session, student, name="已封存", archived=True)

    with pytest.raises(AppError) as gone:
        update_guardian(db_session, archived.id, GuardianUpdateIn(name="新名"), clock=_clock())
    with pytest.raises(AppError) as missing:
        update_guardian(db_session, uuid4(), GuardianUpdateIn(name="新名"), clock=_clock())

    assert (gone.value.status, gone.value.code) == (404, "guardian_not_found")
    assert (missing.value.status, missing.value.code) == (404, "guardian_not_found")
    db_session.refresh(archived)
    assert archived.name == "已封存"


# --- BACKEND-171：archive_guardian ---------------------------------------------------------------


def test_archive_guardian_success(db_session: Session) -> None:
    staff = make_staff(db_session)
    student = make_student(db_session)
    guardian = make_guardian(db_session, student, name="王媽媽", is_primary=True)
    other = make_guardian(db_session, student, name="王爸爸", relation="father")
    _code(db_session, guardian, staff.id, expires_at=_NOW + timedelta(days=1))
    _code(db_session, guardian, staff.id, expires_at=_NOW + timedelta(days=2), used=True)
    _code(db_session, other, staff.id, expires_at=_NOW + timedelta(days=1))

    archive_guardian(db_session, guardian.id, actor=_actor(), meta=_META, clock=_clock())

    assert [g.id for g in list_for_student(db_session, student.id, clock=_clock())] == [other.id]
    db_session.refresh(guardian)
    assert guardian.archived_at == _NOW
    assert guardian.is_primary is False
    assert _unused_codes(db_session, guardian.id) == 0
    used = db_session.execute(
        select(func.count())
        .select_from(ParentBindingCode)
        .where(ParentBindingCode.guardian_id == guardian.id)
    ).scalar_one()
    assert used == 1  # 已使用的碼保留
    assert _unused_codes(db_session, other.id) == 1
    # 未綁定：不寫 guardian.unbind 稽核
    assert _unbind_audits(db_session, guardian.id) == []
    # 封存後主要聯絡人可讓給別人
    update_guardian(db_session, other.id, GuardianUpdateIn(is_primary=True), clock=_clock())


def test_archive_guardian_bound_audit_and_scope(db_session: Session) -> None:
    student = make_student(db_session)
    parent = make_parent(db_session)
    guardian = make_guardian(db_session, student, parent=parent, name="王媽媽")
    actor = _actor()
    assert get_parent_student_ids(db_session, parent.id) == [student.id]

    archive_guardian(db_session, guardian.id, actor=actor, meta=_META, clock=_clock())

    db_session.refresh(guardian)
    assert guardian.parent_account_id == parent.id  # 歷史保留
    assert guardian.archived_at == _NOW
    assert get_parent_student_ids(db_session, parent.id) == []
    audits = _unbind_audits(db_session, guardian.id)
    assert len(audits) == 1
    assert audits[0].before == {"parent_account_id": str(parent.id)}
    assert audits[0].after == {"reason": "archived"}
    assert (audits[0].actor_type, audits[0].actor_id, audits[0].entity_type) == (
        "staff",
        actor.id,
        "guardian",
    )
    assert audits[0].ip == "127.0.0.1"


def test_archive_guardian_not_found(db_session: Session) -> None:
    student = make_student(db_session)
    archived = make_guardian(db_session, student, archived=True)

    with pytest.raises(AppError) as missing:
        archive_guardian(db_session, uuid4(), actor=_actor(), meta=_META, clock=_clock())
    with pytest.raises(AppError) as twice:
        archive_guardian(db_session, archived.id, actor=_actor(), meta=_META, clock=_clock())

    assert (missing.value.status, missing.value.code) == (404, "guardian_not_found")
    assert (twice.value.status, twice.value.code) == (404, "guardian_not_found")


# --- BACKEND-172：unbind_guardian ----------------------------------------------------------------


def test_unbind_guardian_success(db_session: Session) -> None:
    staff = make_staff(db_session)
    student = make_student(db_session)
    parent = make_parent(db_session, display_name="王媽媽")
    guardian = make_guardian(db_session, student, parent=parent, name="王媽媽", is_primary=True)
    _code(db_session, guardian, staff.id, expires_at=_NOW + timedelta(days=1))
    actor = _actor()

    out = unbind_guardian(db_session, guardian.id, actor=actor, meta=_META, clock=_clock())

    assert out.id == guardian.id
    assert out.binding.status == "unbound"
    assert out.binding.parent_display_name is None
    assert out.is_primary is True  # 其他欄位不變
    db_session.refresh(guardian)
    assert guardian.parent_account_id is None
    assert guardian.archived_at is None
    assert _unused_codes(db_session, guardian.id) == 0
    assert get_parent_student_ids(db_session, parent.id) == []
    audits = _unbind_audits(db_session, guardian.id)
    assert len(audits) == 1
    assert audits[0].before == {"parent_account_id": str(parent.id)}
    assert (audits[0].actor_type, audits[0].actor_id) == ("staff", actor.id)
    (listed,) = list_for_student(db_session, student.id, clock=_clock())
    assert listed.binding.status == "unbound"


def test_unbind_guardian_not_bound(db_session: Session) -> None:
    student = make_student(db_session)
    guardian = make_guardian(db_session, student)
    archived_bound = make_guardian(
        db_session, student, parent=make_parent(db_session), relation="father", archived=True
    )

    with pytest.raises(AppError) as exc:
        unbind_guardian(db_session, guardian.id, actor=_actor(), meta=_META, clock=_clock())
    with pytest.raises(AppError) as gone:
        unbind_guardian(db_session, archived_bound.id, actor=_actor(), meta=_META, clock=_clock())
    with pytest.raises(AppError) as missing:
        unbind_guardian(db_session, uuid4(), actor=_actor(), meta=_META, clock=_clock())

    assert (exc.value.status, exc.value.code) == (409, "guardian_not_bound")
    assert (gone.value.status, gone.value.code) == (404, "guardian_not_found")
    assert (missing.value.status, missing.value.code) == (404, "guardian_not_found")
    assert _unbind_audits(db_session, guardian.id) == []


def test_unbind_guardian_keeps_other_binding(db_session: Session) -> None:
    parent = make_parent(db_session)
    student_a = make_student(db_session, name="王小明")
    student_b = make_student(db_session, name="王小華")
    guardian_a = make_guardian(db_session, student_a, parent=parent)
    make_guardian(db_session, student_b, parent=parent)
    assert set(get_parent_student_ids(db_session, parent.id)) == {student_a.id, student_b.id}

    unbind_guardian(db_session, guardian_a.id, actor=_actor(), meta=_META, clock=_clock())

    assert get_parent_student_ids(db_session, parent.id) == [student_b.id]
    # 家長帳號本身保留
    assert parent.status == "active"
