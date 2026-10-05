"""BACKEND-168：監護人服務（學生的監護人清單，含綁定狀態）。BACKEND-169 ``create_guardian``。

BACKEND-170 ``update_guardian``（移植 ivy ``api/students.py::update_guardian``）：部分更新；
``is_primary=True`` 先把同學生其他 primary 改 false 再寫本列，``is_primary=False`` 允許學生沒有
主要聯絡人。
BACKEND-171 ``archive_guardian``（移植 ivy ``delete_guardian`` 的軟刪除）：``archived_at = now``、
``is_primary = false``、刪除未使用綁定碼；已綁定家長時保留 parent_account_id 作歷史，但家長可見範圍
（BACKEND-179 要求 guardian 未封存）立即排除，並稽核 ``guardian.unbind``（after reason=archived）。
BACKEND-172 ``unbind_guardian``：解除綁定（parent_account_id = null）、刪除未使用綁定碼、稽核
``guardian.unbind``；未綁定 409 ``guardian_not_bound``。家長 token 不需失效：可見範圍每次請求重算，
ws 訂閱由定期重驗移除（BACKEND-227）。

三個寫入方法都比照 169：先 ``get_student_or_404(for_update=True)`` 鎖學生列序列化同一學生的監護人
異動，再以 FOR UPDATE + populate_existing 重讀 guardian 列判斷是否已封存。
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.core.clock import Clock
from app.core.errors import ConflictError, NotFoundError
from app.models.parents import Guardian, ParentBindingCode
from app.repositories.students import get_student_or_404
from app.schemas.guardians import (
    GuardianBindingOut,
    GuardianCreateIn,
    GuardianOut,
    GuardianUpdateIn,
)
from app.services import audit_service

if TYPE_CHECKING:
    from app.api.deps import CurrentStaff
    from app.core.request_meta import RequestMeta


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
    latest_expiry = _latest_code_expiry(session, unbound_ids, clock)
    return [to_guardian_out(g, latest_expiry.get(g.id)) for g in guardians]


def _latest_code_expiry(
    session: Session, guardian_ids: list[UUID], clock: Clock
) -> dict[UUID, datetime]:
    """各 guardian 最新「未使用且未過期」綁定碼的到期時間（沒有碼的不在結果內）。"""
    if not guardian_ids:
        return {}
    rows = session.execute(
        select(ParentBindingCode.guardian_id, func.max(ParentBindingCode.expires_at))
        .where(
            ParentBindingCode.guardian_id.in_(guardian_ids),
            ParentBindingCode.used_at.is_(None),
            ParentBindingCode.expires_at > clock.now(),
        )
        .group_by(ParentBindingCode.guardian_id)
    )
    return {guardian_id: expires_at for guardian_id, expires_at in rows}


def _guardian_out(session: Session, guardian: Guardian, clock: Clock) -> GuardianOut:
    expiry = None
    if guardian.parent_account_id is None:
        expiry = _latest_code_expiry(session, [guardian.id], clock).get(guardian.id)
    return to_guardian_out(guardian, expiry)


def create_guardian(
    session: Session, student_id: UUID, data: GuardianCreateIn, *, clock: Clock
) -> GuardianOut:
    """學生不存在 404、已封存 409 ``student_archived``。

    鎖定學生列（FOR UPDATE）序列化同一學生的監護人異動；``is_primary=True`` 時先把同學生其他
    未封存 guardian 的 is_primary 設 false，再 insert（避免 ``uq_guardians_one_primary`` 衝突）。
    """
    student = get_student_or_404(session, student_id, include_archived=True, for_update=True)
    if student.archived_at is not None:
        raise ConflictError("student_archived", "此學生已封存，無法新增監護人")
    if data.is_primary:
        session.execute(
            update(Guardian)
            .where(
                Guardian.student_id == student_id,
                Guardian.is_primary.is_(True),
                Guardian.archived_at.is_(None),
            )
            .values(is_primary=False)
        )
    guardian = Guardian(student_id=student_id, **data.model_dump())
    session.add(guardian)
    session.flush()
    return to_guardian_out(guardian, None)


def _not_found() -> NotFoundError:
    return NotFoundError("guardian_not_found", "找不到監護人")


def _lock_guardian_for_write(session: Session, guardian_id: UUID) -> Guardian:
    """不存在或已封存 → 404。先鎖學生列（序列化同一學生的監護人異動），再以 FOR UPDATE 重讀本列。"""
    student_id = session.execute(
        select(Guardian.student_id).where(
            Guardian.id == guardian_id, Guardian.archived_at.is_(None)
        )
    ).scalar_one_or_none()
    if student_id is None:
        raise _not_found()
    get_student_or_404(session, student_id, include_archived=True, for_update=True)
    guardian = session.execute(
        select(Guardian)
        .where(Guardian.id == guardian_id)
        # student / parent_account 為 lazy='joined'：只鎖 guardians，重讀上鎖後的值
        .with_for_update(of=Guardian)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if guardian is None or guardian.archived_at is not None:
        raise _not_found()
    return guardian


def _demote_other_primaries(session: Session, guardian: Guardian) -> None:
    session.execute(
        update(Guardian)
        .where(
            Guardian.student_id == guardian.student_id,
            Guardian.id != guardian.id,
            Guardian.is_primary.is_(True),
            Guardian.archived_at.is_(None),
        )
        .values(is_primary=False)
    )


def _delete_unused_codes(session: Session, guardian_id: UUID) -> None:
    session.execute(
        delete(ParentBindingCode).where(
            ParentBindingCode.guardian_id == guardian_id, ParentBindingCode.used_at.is_(None)
        )
    )


def _record_unbind(
    session: Session,
    guardian: Guardian,
    *,
    actor: CurrentStaff,
    meta: RequestMeta,
    reason: str | None,
) -> None:
    audit_service.record(
        session,
        actor=audit_service.Actor.staff(actor),
        action="guardian.unbind",
        entity_type="guardian",
        entity_id=guardian.id,
        before={"parent_account_id": guardian.parent_account_id},
        after=None if reason is None else {"reason": reason},
        meta=meta,
    )


def update_guardian(
    session: Session, guardian_id: UUID, data: GuardianUpdateIn, *, clock: Clock
) -> GuardianOut:
    """只更新有給的欄位；``is_primary=True`` 先取消同學生其他 primary（互斥）。

    receives_notifications / can_pickup 的變更立即影響通知收件人與接送核對（每次查詢即時計算）。
    """
    guardian = _lock_guardian_for_write(session, guardian_id)
    changes = data.model_dump(exclude_unset=True)
    if changes.get("is_primary") is True:
        _demote_other_primaries(session, guardian)
    for field, value in changes.items():
        setattr(guardian, field, value)
    session.flush()
    return _guardian_out(session, guardian, clock)


def archive_guardian(
    session: Session, guardian_id: UUID, *, actor: CurrentStaff, meta: RequestMeta, clock: Clock
) -> None:
    guardian = _lock_guardian_for_write(session, guardian_id)
    if guardian.parent_account_id is not None:
        # 保留 parent_account_id 作歷史；封存後 BACKEND-179 的可見範圍已排除，等同解除綁定
        _record_unbind(session, guardian, actor=actor, meta=meta, reason="archived")
    guardian.archived_at = clock.now()
    guardian.is_primary = False
    _delete_unused_codes(session, guardian.id)
    session.flush()


def unbind_guardian(
    session: Session, guardian_id: UUID, *, actor: CurrentStaff, meta: RequestMeta, clock: Clock
) -> GuardianOut:
    guardian = _lock_guardian_for_write(session, guardian_id)
    if guardian.parent_account_id is None:
        raise ConflictError("guardian_not_bound", "此監護人尚未綁定家長帳號")
    _record_unbind(session, guardian, actor=actor, meta=meta, reason=None)
    guardian.parent_account_id = None
    _delete_unused_codes(session, guardian.id)
    session.flush()
    return _guardian_out(session, guardian, clock)
