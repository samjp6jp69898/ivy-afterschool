"""BACKEND-404：接送請求的回應組裝與 ws 推播（佇列、建立、狀態轉換、自動過期共用）。

移植 ivy ``api/dismissal_calls.py::_build_calls_out_bulk`` 的批次解析；名稱來源：

- requested_by：員工 → display_name；家長 → 該家長對此學生的 guardian 名稱（例如「王媽媽」），查無
  時用家長 display_name。
- picked_up_by：guardian.name 或代理授權的 proxy_name；override 且兩者皆無 →「老師確認交付」。

``build_*`` 的 SQL 固定（學生、員工 / 家長名稱、guardian、代理授權、作業進度各一條），不 N+1。
``publish_request_change`` 同一交易對同一請求多次呼叫只推最後狀態：先登記在 session.info，最外層
commit 前（before_commit）才重讀請求並組資料，交給 BACKEND-224 在 commit 後送出。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any, Final
from uuid import UUID

from sqlalchemy import ColumnElement, Select, and_, event, literal, or_, select
from sqlalchemy.orm import Session, SessionTransaction

from app.core.clock import Clock, to_taipei
from app.models.account import StaffUser
from app.models.homework import HomeworkDailyProgress
from app.models.parents import Guardian, ParentAccount
from app.models.pickup import OPEN_STATUSES, PickupAuthorization, PickupRequest
from app.realtime.publish import broadcast_after_commit
from app.repositories.students import StudentBrief, student_brief_map
from app.schemas.pickup import ParentPickupRequestOut, PickupRequestOut, PickupStudentOut

OVERRIDE_PICKER_NAME: Final = "老師確認交付"
CAN_MARK_ARRIVED_STATUSES: Final = frozenset({"pending", "acknowledged"})
PUBLISH_KEYS: Final = "pickup_publish_request_ids"
_PUBLISH_HOOKED: Final = "pickup_publish_hooked"


def _hm(value: time | None) -> str | None:
    return value.strftime("%H:%M") if value is not None else None


def _taipei_hm(value: datetime | None) -> str | None:
    return to_taipei(value).strftime("%H:%M") if value is not None else None


def needs_reply(request: PickupRequest) -> bool:
    """非終態，且沒有回覆或只有沒有 ETA 的自動回覆（「已通知老師」）。"""
    if request.status not in OPEN_STATUSES:
        return False
    return request.reply_source is None or (
        request.reply_source == "auto" and request.reply_ready_eta is None
    )


@dataclass(frozen=True)
class _Lookups:
    students: dict[UUID, StudentBrief]
    staff_names: dict[UUID, str]
    parent_names: dict[UUID, str | None]
    guardian_names: dict[UUID, str]
    # (家長帳號, 學生) → guardian 名稱（未封存優先）
    parent_guardian_names: dict[tuple[UUID, UUID], str]
    proxy_names: dict[UUID, str]
    progress: dict[tuple[UUID, date], tuple[str, time | None]]


def _actor_names(
    session: Session, staff_ids: set[UUID], parent_ids: set[UUID]
) -> tuple[dict[UUID, str], dict[UUID, str | None]]:
    """員工與家長名稱合併成一次 UNION 查詢。"""
    parts: list[Select[Any, Any, Any]] = []
    if staff_ids:
        parts.append(
            select(literal("staff"), StaffUser.id, StaffUser.display_name).where(
                StaffUser.id.in_(staff_ids)
            )
        )
    if parent_ids:
        parts.append(
            select(literal("parent"), ParentAccount.id, ParentAccount.display_name).where(
                ParentAccount.id.in_(parent_ids)
            )
        )
    if not parts:
        return {}, {}
    stmt = parts[0] if len(parts) == 1 else parts[0].union_all(parts[1])
    staff: dict[UUID, str] = {}
    parents: dict[UUID, str | None] = {}
    for kind, actor_id, name in session.execute(stmt):
        if kind == "staff":
            staff[actor_id] = name
        else:
            parents[actor_id] = name
    return staff, parents


def _load_lookups(session: Session, requests: Sequence[PickupRequest]) -> _Lookups:
    student_ids = {r.student_id for r in requests}
    parent_requesters = {r.requested_by_id for r in requests if r.requested_by_type == "parent"}
    staff_ids = {r.requested_by_id for r in requests if r.requested_by_type == "staff"}
    staff_ids |= {r.replied_by for r in requests if r.replied_by is not None}
    staff_ids |= {r.completed_by for r in requests if r.completed_by is not None}
    guardian_ids = {
        r.picked_up_by_guardian_id for r in requests if r.picked_up_by_guardian_id is not None
    }
    authorization_ids = {
        r.picked_up_by_authorization_id
        for r in requests
        if r.picked_up_by_authorization_id is not None
    }

    staff_names, parent_names = _actor_names(session, staff_ids, parent_requesters)

    guardian_names: dict[UUID, str] = {}
    parent_guardian_names: dict[tuple[UUID, UUID], str] = {}
    conditions: list[ColumnElement[bool]] = []
    if guardian_ids:
        conditions.append(Guardian.id.in_(guardian_ids))
    if parent_requesters:
        conditions.append(
            and_(
                Guardian.parent_account_id.in_(parent_requesters),
                Guardian.student_id.in_(student_ids),
            )
        )
    if conditions:
        rows = session.execute(
            select(
                Guardian.id,
                Guardian.parent_account_id,
                Guardian.student_id,
                Guardian.name,
                Guardian.archived_at,
            )
            .where(or_(*conditions))
            # 未封存的排在後面，覆寫同一 (家長, 學生) 的封存紀錄
            .order_by(Guardian.archived_at.desc().nulls_last())
        )
        for guardian_id, parent_id, student_id, name, _archived_at in rows:
            guardian_names[guardian_id] = name
            if parent_id is not None:
                parent_guardian_names[(parent_id, student_id)] = name

    proxy_names: dict[UUID, str] = {}
    if authorization_ids:
        proxy_names = {
            auth_id: name
            for auth_id, name in session.execute(
                select(PickupAuthorization.id, PickupAuthorization.proxy_name).where(
                    PickupAuthorization.id.in_(authorization_ids)
                )
            )
        }

    wanted = {(r.student_id, r.service_date) for r in requests}
    progress: dict[tuple[UUID, date], tuple[str, time | None]] = {}
    if wanted:
        for student_id, service_date, overall, eta in session.execute(
            select(
                HomeworkDailyProgress.student_id,
                HomeworkDailyProgress.service_date,
                HomeworkDailyProgress.overall_status,
                HomeworkDailyProgress.ready_eta,
            ).where(
                HomeworkDailyProgress.student_id.in_(student_ids),
                HomeworkDailyProgress.service_date.in_({d for _, d in wanted}),
            )
        ):
            if (student_id, service_date) in wanted:
                progress[(student_id, service_date)] = (overall, eta)

    return _Lookups(
        students=student_brief_map(session, student_ids),
        staff_names=staff_names,
        parent_names=parent_names,
        guardian_names=guardian_names,
        parent_guardian_names=parent_guardian_names,
        proxy_names=proxy_names,
        progress=progress,
    )


def _requested_by_name(request: PickupRequest, lookups: _Lookups) -> str | None:
    if request.requested_by_type == "staff":
        return lookups.staff_names.get(request.requested_by_id)
    guardian_name = lookups.parent_guardian_names.get((request.requested_by_id, request.student_id))
    return guardian_name or lookups.parent_names.get(request.requested_by_id)


def _picked_up_by_name(request: PickupRequest, lookups: _Lookups) -> str | None:
    if request.picked_up_by_guardian_id is not None:
        return lookups.guardian_names.get(request.picked_up_by_guardian_id)
    if request.picked_up_by_authorization_id is not None:
        return lookups.proxy_names.get(request.picked_up_by_authorization_id)
    if request.completion_method == "override":
        return OVERRIDE_PICKER_NAME
    return None


def _admin_view(request: PickupRequest, lookups: _Lookups) -> PickupRequestOut:
    brief = lookups.students[request.student_id]
    overall, eta = lookups.progress.get((request.student_id, request.service_date), (None, None))
    return PickupRequestOut(
        id=request.id,
        student=PickupStudentOut(
            id=brief.id,
            student_no=brief.student_no,
            name=brief.name,
            grade_level=brief.grade_level,
            class_id=brief.class_id,
            class_name=brief.class_name,
        ),
        service_date=request.service_date,
        source=request.source,
        requested_by_type=request.requested_by_type,
        requested_by_name=_requested_by_name(request, lookups),
        expected_arrival_at=_taipei_hm(request.expected_arrival_at),
        status=request.status,
        homework_status_at_request=request.homework_status_at_request,
        current_homework_status=overall,
        current_ready_eta=_hm(eta),
        reply_ready_eta=_hm(request.reply_ready_eta),
        reply_message=request.reply_message,
        reply_source=request.reply_source,
        replied_at=request.replied_at,
        replied_by_name=lookups.staff_names.get(request.replied_by) if request.replied_by else None,
        needs_reply=needs_reply(request),
        arrived_at=request.arrived_at,
        completed_at=request.completed_at,
        completed_by_name=(
            lookups.staff_names.get(request.completed_by) if request.completed_by else None
        ),
        completion_method=request.completion_method,
        picked_up_by_name=_picked_up_by_name(request, lookups),
        cancelled_at=request.cancelled_at,
        cancel_reason=request.cancel_reason,
        created_at=request.created_at,
    )


def _parent_view(request: PickupRequest, lookups: _Lookups) -> ParentPickupRequestOut:
    return ParentPickupRequestOut(
        id=request.id,
        student_id=request.student_id,
        student_name=lookups.students[request.student_id].name,
        service_date=request.service_date,
        status=request.status,
        expected_arrival_at=_taipei_hm(request.expected_arrival_at),
        reply_ready_eta=_hm(request.reply_ready_eta),
        reply_message=request.reply_message,
        reply_source=request.reply_source,
        replied_at=request.replied_at,
        arrived_at=request.arrived_at,
        completed_at=request.completed_at,
        picked_up_by_name=_picked_up_by_name(request, lookups),
        cancelled_at=request.cancelled_at,
        created_at=request.created_at,
        can_cancel=request.status in OPEN_STATUSES,
        can_mark_arrived=request.status in CAN_MARK_ARRIVED_STATUSES,
    )


def build_request_views(
    session: Session, requests: Sequence[PickupRequest]
) -> list[PickupRequestOut]:
    if not requests:
        return []
    lookups = _load_lookups(session, requests)
    return [_admin_view(request, lookups) for request in requests]


def build_parent_request_views(
    session: Session, requests: Sequence[PickupRequest]
) -> list[ParentPickupRequestOut]:
    """家長端：不含員工姓名。"""
    if not requests:
        return []
    lookups = _load_lookups(session, requests)
    return [_parent_view(request, lookups) for request in requests]


def _flush_publishes(session: Session) -> None:
    # begin_nested 的 savepoint 釋放也會發 before_commit：等最外層 commit 才組資料
    if session.in_nested_transaction():
        return
    pending: dict[UUID, Clock] = session.info.pop(PUBLISH_KEYS, {})
    if not pending:
        return
    requests = list(
        session.execute(
            select(PickupRequest)
            .where(PickupRequest.id.in_(pending))
            .execution_options(populate_existing=True)
        ).scalars()
    )
    lookups = _load_lookups(session, requests)
    for request in requests:
        broadcast_after_commit(
            session,
            topic="pickup",
            type="pickup.request_updated",
            data=_admin_view(request, lookups).model_dump(),
            student_id=request.student_id,
            parent_data=_parent_view(request, lookups).model_dump(),
            clock=pending[request.id],
        )


def _clear_publishes(session: Session, previous_transaction: SessionTransaction) -> None:
    if previous_transaction.nested:
        return
    session.info.pop(PUBLISH_KEYS, None)


def publish_request_change(session: Session, request: PickupRequest, *, clock: Clock) -> None:
    """登記推播；commit 時推 admin:pickup 完整資料與 student:<id> 家長版，rollback 不推。"""
    if not session.info.get(_PUBLISH_HOOKED):
        event.listen(session, "before_commit", _flush_publishes)
        event.listen(session, "after_soft_rollback", _clear_publishes)
        session.info[_PUBLISH_HOOKED] = True
    # 與 run_after_commit 相同：先開交易，之後的 rollback 才一定會觸發 after_soft_rollback 清掉登記
    if not session.in_transaction():
        session.begin()
    keys: dict[UUID, Clock] = session.info.setdefault(PUBLISH_KEYS, {})
    keys[request.id] = clock
