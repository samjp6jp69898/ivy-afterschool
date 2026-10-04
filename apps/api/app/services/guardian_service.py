"""BACKEND-168：監護人服務（學生的監護人清單，含綁定狀態）。"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.clock import Clock
from app.models.parents import Guardian, ParentBindingCode
from app.repositories.students import get_student_or_404
from app.schemas.guardians import GuardianBindingOut, GuardianOut


def to_guardian_out(guardian: Guardian, latest_code_expires_at: datetime | None) -> GuardianOut:
    """binding：已綁家長 → bound；否則有未過期且未使用的碼 → code_issued；否則 unbound。"""
    if guardian.parent_account_id is not None:
        account = guardian.parent_account
        binding = GuardianBindingOut(
            status="bound", parent_display_name=account.display_name if account else None
        )
    elif latest_code_expires_at is not None:
        binding = GuardianBindingOut(status="code_issued", code_expires_at=latest_code_expires_at)
    else:
        binding = GuardianBindingOut(status="unbound")
    return GuardianOut(
        id=guardian.id,
        student_id=guardian.student_id,
        name=guardian.name,
        relation=guardian.relation,
        phone=guardian.phone,
        is_primary=guardian.is_primary,
        can_pickup=guardian.can_pickup,
        receives_notifications=guardian.receives_notifications,
        binding=binding,
    )


def list_for_student(session: Session, student_id: UUID, *, clock: Clock) -> list[GuardianOut]:
    """封存學生也可查；只列未封存 guardian，排序 is_primary desc、created_at。"""
    get_student_or_404(session, student_id, include_archived=True)
    guardians = list(
        session.execute(
            select(Guardian)
            .where(Guardian.student_id == student_id, Guardian.archived_at.is_(None))
            .order_by(Guardian.is_primary.desc(), Guardian.created_at, Guardian.id)
        )
        .scalars()
        .unique()
    )
    unbound_ids = [g.id for g in guardians if g.parent_account_id is None]
    latest_expiry: dict[UUID, datetime] = {}
    if unbound_ids:
        rows = session.execute(
            select(ParentBindingCode.guardian_id, func.max(ParentBindingCode.expires_at))
            .where(
                ParentBindingCode.guardian_id.in_(unbound_ids),
                ParentBindingCode.used_at.is_(None),
                ParentBindingCode.expires_at > clock.now(),
            )
            .group_by(ParentBindingCode.guardian_id)
        )
        latest_expiry = {guardian_id: expires_at for guardian_id, expires_at in rows}
    return [to_guardian_out(g, latest_expiry.get(g.id)) for g in guardians]
