"""接送請求查詢與回覆同步（domain_spec M7）。

- BACKEND-414：``get_queue``（後台接送佇列）。移植 ivy
  ``api/dismissal_calls.py::list_dismissal_calls`` 的「只看當日」；去掉班級篩選。
- BACKEND-415：``get_roster``（接送 POS 學生卡）。移植 ivy
  ``services/dismissal_pos.py::get_pos_status`` 的「一次查出當日請假名單」；去掉娃娃車、tenant、
  POS 標記請假。
- BACKEND-416：``list_today_requests_for_parent``（家長端今日請求）。移植 ivy
  ``api/parent_portal/dismissal_calls.py::list_dismissal_notices``。
- BACKEND-413：``sync_open_request_reply``（作業完成或 ETA 變動時同步進行中請求的回覆）。
- BACKEND-406：``create_request``（家長「我要來接」/ 員工代建；自動回覆、同學生同日只一筆非終態）。
- BACKEND-407 / 408 / 409 / 411：``reply_request`` / ``acknowledge_request`` / ``mark_arrived`` /
  ``cancel_request``（都經 BACKEND-405 條件式狀態轉換）。
- BACKEND-410：``complete_request``（監護人 / 強制完成、出勤改 left、通知家長、override 寫 audit）。

鎖序：進度列 → 請求列 → 出勤列（reply_request 先鎖進度列；complete_request 由條件式 UPDATE 鎖請求列
後才更新出勤）。
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING, Any, Final
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.clock import Clock, combine_taipei, to_taipei
from app.core.errors import AppError, ConflictError, ForbiddenError, NotFoundError
from app.core.permissions import Permission
from app.core.settings_registry import HOMEWORK_DEFAULTS, PICKUP_WINDOW
from app.models.attendance import StudentAttendance
from app.models.classes import SchoolClass
from app.models.homework import HomeworkDailyProgress
from app.models.leaves import StudentLeave
from app.models.parents import Guardian
from app.models.pickup import OPEN_STATUSES, UQ_ONE_OPEN, PickupAuthorization, PickupRequest
from app.models.students import Student
from app.notifications.events import Event
from app.notifications.recipients import Recipient, parent_recipients, staff_recipients
from app.notifications.service import enqueue
from app.repositories.students import get_student_or_404
from app.schemas.pickup import (
    ParentPickupRequestCreateIn,
    ParentPickupRequestOut,
    PickupCancelIn,
    PickupCompleteIn,
    PickupQueueCountsOut,
    PickupQueueOut,
    PickupQueueQuery,
    PickupReplyIn,
    RosterClassOut,
    RosterOpenRequestOut,
    RosterOut,
    RosterQuery,
    RosterStudentOut,
    StaffPickupRequestCreateIn,
)
from app.services.attendance_service import mark_left_by_pickup
from app.services.audit_service import Actor, record
from app.services.parent_scope import assert_parent_owns_student, get_parent_student_ids
from app.services.pickup.auto_reply import DONE_REPLY_TEXT, ProgressSnapshot, compute_auto_reply
from app.services.pickup.transitions import transition_request
from app.services.pickup.views import (
    OVERRIDE_PICKER_NAME,
    build_parent_request_views,
    build_request_views,
    needs_reply,
    publish_request_change,
)
from app.services.service_calendar import is_service_day
from app.services.settings_service import get_setting

if TYPE_CHECKING:
    from app.api.deps import CurrentParent, CurrentStaff
    from app.core.request_meta import RequestMeta

UNASSIGNED_CLASS_NAME: Final = "未分班"
ACK_DEFAULT_MESSAGE: Final = "老師已確認接送請求"
# 家長填的預計抵達時間可以比現在早這麼多（網路延遲、邊走邊按）
ARRIVAL_PAST_TOLERANCE: Final = timedelta(minutes=5)
UNAVAILABLE_ATTENDANCE: Final = frozenset({"leave", "absent", "left"})
# open 佇列的狀態順序：已抵達最優先
_OPEN_ORDER: Final = {"arrived": 0, "pending": 1, "acknowledged": 2}


def _hm(value: time | None) -> str | None:
    return value.strftime("%H:%M") if value is not None else None


def _taipei_hm(value: datetime | None) -> str | None:
    return to_taipei(value).strftime("%H:%M") if value is not None else None


def _open_key(request: PickupRequest) -> tuple[int, bool, datetime, datetime, UUID]:
    """狀態順序 → 預計到達（null 最後）→ 建立時間。"""
    eta = request.expected_arrival_at
    return (
        _OPEN_ORDER[request.status],
        eta is None,
        eta or request.created_at,
        request.created_at,
        request.id,
    )


def _closed_at(request: PickupRequest) -> datetime:
    return request.completed_at or request.cancelled_at or request.updated_at


def get_queue(session: Session, query: PickupQueueQuery, *, clock: Clock) -> PickupQueueOut:
    """該日全部請求，分 open（非終態）/ closed（終態，最近結束的在前）。"""
    d = query.date or clock.today()
    requests = list(
        session.execute(select(PickupRequest).where(PickupRequest.service_date == d)).scalars()
    )
    open_requests = sorted((r for r in requests if r.status in OPEN_STATUSES), key=_open_key)
    closed_requests = sorted(
        (r for r in requests if r.status not in OPEN_STATUSES),
        key=lambda r: (_closed_at(r), r.id),
        reverse=True,
    )
    views = build_request_views(session, [*open_requests, *closed_requests])
    open_views, closed_views = views[: len(open_requests)], views[len(open_requests) :]
    statuses = Counter(r.status for r in open_requests)
    return PickupQueueOut(
        date=d,
        open=open_views,
        closed=closed_views,
        counts=PickupQueueCountsOut(
            pending=statuses["pending"],
            acknowledged=statuses["acknowledged"],
            arrived=statuses["arrived"],
            needs_reply=sum(1 for view in open_views if view.needs_reply),
        ),
    )


def get_roster(session: Session, query: RosterQuery, *, clock: Clock) -> RosterOut:
    """active 未封存學生依班級分組（班級 sort_order、班名；未分班最後），組內依學號。

    SQL 固定：學生、出勤（join 請假）、作業進度、進行中請求、active 代理授權數。
    """
    d = query.date or clock.today()
    stmt = (
        select(
            Student.id,
            Student.student_no,
            Student.name,
            Student.grade_level,
            Student.class_id,
            SchoolClass.name,
        )
        .outerjoin(SchoolClass, SchoolClass.id == Student.class_id)
        .where(Student.status == "active", Student.archived_at.is_(None))
        .order_by(
            SchoolClass.id.is_(None),
            SchoolClass.sort_order,
            SchoolClass.name,
            Student.class_id,
            Student.student_no,
            Student.id,
        )
    )
    if query.class_id is not None:
        stmt = stmt.where(Student.class_id == query.class_id)
    students = session.execute(stmt).all()
    ids = [row[0] for row in students]

    attendance: dict[UUID, tuple[Any, ...]] = {}
    progress: dict[UUID, tuple[Any, ...]] = {}
    open_requests: dict[UUID, PickupRequest] = {}
    authorizations: dict[UUID, int] = {}
    if ids:
        attendance = {
            row[0]: tuple(row[1:])
            for row in session.execute(
                select(
                    StudentAttendance.student_id,
                    StudentAttendance.status,
                    StudentAttendance.check_in_at,
                    StudentAttendance.check_out_at,
                    StudentLeave.leave_type,
                )
                .outerjoin(StudentLeave, StudentLeave.id == StudentAttendance.leave_id)
                .where(StudentAttendance.student_id.in_(ids), StudentAttendance.service_date == d)
            )
        }
        progress = {
            row[0]: tuple(row[1:])
            for row in session.execute(
                select(
                    HomeworkDailyProgress.student_id,
                    HomeworkDailyProgress.overall_status,
                    HomeworkDailyProgress.ready_eta,
                ).where(
                    HomeworkDailyProgress.student_id.in_(ids),
                    HomeworkDailyProgress.service_date == d,
                )
            )
        }
        # uq_pickup_requests_one_open：同一學生同一天至多一筆非終態請求
        open_requests = {
            request.student_id: request
            for request in session.execute(
                select(PickupRequest).where(
                    PickupRequest.student_id.in_(ids),
                    PickupRequest.service_date == d,
                    PickupRequest.status.in_(OPEN_STATUSES),
                )
            ).scalars()
        }
        authorizations = {
            student_id: count
            for student_id, count in session.execute(
                select(PickupAuthorization.student_id, func.count())
                .where(
                    PickupAuthorization.student_id.in_(ids),
                    PickupAuthorization.service_date == d,
                    PickupAuthorization.status == "active",
                )
                .group_by(PickupAuthorization.student_id)
            )
        }

    groups: dict[UUID | None, RosterClassOut] = {}
    by_class: defaultdict[UUID | None, list[RosterStudentOut]] = defaultdict(list)
    for student_id, student_no, name, grade_level, class_id, class_name in students:
        if class_id not in groups:
            groups[class_id] = RosterClassOut(
                class_id=class_id,
                class_name=class_name if class_id is not None else UNASSIGNED_CLASS_NAME,
                students=[],
            )
        status, check_in_at, check_out_at, leave_type = attendance.get(
            student_id, (None, None, None, None)
        )
        overall, eta = progress.get(student_id, (None, None))
        request = open_requests.get(student_id)
        by_class[class_id].append(
            RosterStudentOut(
                student_id=student_id,
                student_no=student_no,
                name=name,
                grade_level=grade_level,
                attendance_status=status,
                check_in_at=check_in_at,
                check_out_at=check_out_at,
                leave_type=leave_type if status == "leave" else None,
                homework_status=overall,
                ready_eta=_hm(eta),
                open_request=(
                    RosterOpenRequestOut(
                        id=request.id,
                        status=request.status,
                        expected_arrival_at=_taipei_hm(request.expected_arrival_at),
                        needs_reply=needs_reply(request),
                    )
                    if request is not None
                    else None
                ),
                active_authorization_count=authorizations.get(student_id, 0),
            )
        )
    return RosterOut(
        date=d,
        classes=[
            group.model_copy(update={"students": by_class[class_id]})
            for class_id, group in groups.items()
        ],
    )


def list_today_requests_for_parent(
    session: Session, parent_id: UUID, *, clock: Clock
) -> list[ParentPickupRequestOut]:
    """家長全部小孩今天的請求（含終態），新到舊；不含員工姓名。"""
    student_ids = get_parent_student_ids(session, parent_id)
    if not student_ids:
        return []
    requests = list(
        session.execute(
            select(PickupRequest)
            .where(
                PickupRequest.student_id.in_(student_ids),
                PickupRequest.service_date == clock.today(),
            )
            .order_by(PickupRequest.created_at.desc(), PickupRequest.id)
        ).scalars()
    )
    return build_parent_request_views(session, requests)


def sync_open_request_reply(
    session: Session, student_id: UUID, service_date: date, *, clock: Clock
) -> PickupRequest | None:
    """作業模組（BACKEND-376 / 382）寫入進度後呼叫；先鎖請求列，與員工回覆、完成序列化。

    - 作業 done → 回覆一律改為完成文案（覆蓋先前的員工回覆，作業完成是最終事實）。
    - 未完成：員工回覆不動；否則依 ``compute_auto_reply`` 重算，與現值不同才更新。
    有更新才推播；不發 pickup.replied（家長已由 homework 事件得知）。
    """
    request = session.execute(
        select(PickupRequest)
        .where(
            PickupRequest.student_id == student_id,
            PickupRequest.service_date == service_date,
            PickupRequest.status.in_(OPEN_STATUSES),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if request is None:
        return None

    progress = session.execute(
        select(HomeworkDailyProgress).where(
            HomeworkDailyProgress.student_id == student_id,
            HomeworkDailyProgress.service_date == service_date,
        )
    ).scalar_one_or_none()

    if progress is not None and progress.overall_status == "done":
        request.reply_message = DONE_REPLY_TEXT
        request.reply_ready_eta = None
        request.reply_source = "auto"
        request.replied_by = None
        request.replied_at = clock.now()
    elif request.reply_source == "staff":
        # 尊重員工覆寫：ETA 變動不改員工回覆
        return request
    else:
        reply = compute_auto_reply(
            ProgressSnapshot(progress.overall_status, progress.ready_eta, progress.note)
            if progress is not None
            else None,
            get_setting(session, HOMEWORK_DEFAULTS),
        )
        current = (request.reply_ready_eta, request.reply_message, request.reply_source)
        if current == (reply.reply_ready_eta, reply.reply_message, reply.reply_source):
            return request
        request.reply_ready_eta = reply.reply_ready_eta
        request.reply_message = reply.reply_message
        request.reply_source = reply.reply_source
        # DB-025 ck_pickup_requests_reply：沒有回覆來源時 replied_at 也要清空
        request.replied_at = clock.now() if reply.reply_source is not None else None

    session.flush()
    publish_request_change(session, request, clock=clock)
    return request


def _parse_hm(value: str) -> time:
    hour, minute = value.split(":")
    return time(int(hour), int(minute))


def _student_name(session: Session, student_id: UUID) -> str:
    return session.execute(select(Student.name).where(Student.id == student_id)).scalar_one()


def _operators(session: Session) -> list[Recipient]:
    return staff_recipients(session, permission=Permission.PICKUP_OPERATE)


def _actor_id(actor: Actor) -> UUID:
    if actor.id is None:
        raise ValueError(f"actor.type={actor.type} 必須帶 actor.id")
    return actor.id


def _check_requester(session: Session, student_id: UUID, actor: Actor) -> Student:
    if actor.type == "parent":
        parent_id = _actor_id(actor)
        student = assert_parent_owns_student(session, parent_id, student_id, for_write=True)
        can_pickup = session.execute(
            select(Guardian.can_pickup).where(
                Guardian.student_id == student_id,
                Guardian.parent_account_id == parent_id,
                Guardian.archived_at.is_(None),
            )
        ).scalars()
        if not any(can_pickup):
            raise ForbiddenError("此監護人未被設定為可接送", code="pickup_not_allowed")
        return student
    student = get_student_or_404(session, student_id)
    if student.status != "active":
        raise ConflictError("student_not_active", "學生目前不在學，無法建立接送請求")
    return student


def create_request(
    session: Session,
    data: ParentPickupRequestCreateIn | StaffPickupRequestCreateIn,
    *,
    actor: Actor,
    clock: Clock,
) -> PickupRequest:
    """家長「我要來接」或員工代建；依當日作業進度立即自動回覆。

    同學生同日只能有一筆非終態請求：以 savepoint 包住寫入，partial unique index
    ``uq_pickup_requests_one_open`` 讓後到者在前者 commit 後拋 23505 → 409（家長連點、家長與員工
    同時建立都安全，不需應用層鎖）。移植 ivy ``create_dismissal_notice`` 的資格檢查與「伺服器
    計算時間」、``_db_create_dismissal_call`` 的員工代建；去掉 classroom_id、LINE 群組推播、
    client_request_id。
    """
    student = _check_requester(session, data.student_id, actor)
    d = clock.today()
    if not is_service_day(session, d):
        raise ConflictError("not_service_day", "今天不是營業日")
    now = clock.now()
    window = get_setting(session, PICKUP_WINDOW)
    is_parent = actor.type == "parent"
    if is_parent:
        local = to_taipei(now).time()
        if not _parse_hm(window.request_start) <= local <= _parse_hm(window.request_end):
            raise ConflictError(
                "pickup_window_closed",
                f"接送請求開放時間為 {window.request_start}～{window.request_end}",
                details={"request_start": window.request_start, "request_end": window.request_end},
            )
    arrived = isinstance(data, ParentPickupRequestCreateIn) and data.arrived
    expected_arrival_at = None
    if not arrived and data.expected_arrival_at is not None:
        expected_arrival_at = combine_taipei(d, _parse_hm(data.expected_arrival_at))
        if expected_arrival_at < now - ARRIVAL_PAST_TOLERANCE:
            raise AppError("expected_arrival_in_past", "預計抵達時間已經過了", status=422)
        if expected_arrival_at > combine_taipei(d, _parse_hm(window.latest_expected_arrival)):
            raise AppError(
                "expected_arrival_too_late",
                f"預計抵達時間最晚為 {window.latest_expected_arrival}",
                status=422,
            )

    attendance_status = session.execute(
        select(StudentAttendance.status).where(
            StudentAttendance.student_id == student.id, StudentAttendance.service_date == d
        )
    ).scalar_one_or_none()
    if attendance_status in UNAVAILABLE_ATTENDANCE:
        raise ConflictError(
            "student_not_available",
            "學生今天請假、缺席或已離班",
            details={"attendance_status": attendance_status},
        )

    progress = session.execute(
        select(HomeworkDailyProgress).where(
            HomeworkDailyProgress.student_id == student.id, HomeworkDailyProgress.service_date == d
        )
    ).scalar_one_or_none()
    reply = compute_auto_reply(
        ProgressSnapshot(progress.overall_status, progress.ready_eta, progress.note)
        if progress is not None
        else None,
        get_setting(session, HOMEWORK_DEFAULTS),
    )
    request = PickupRequest(
        student_id=student.id,
        service_date=d,
        source="parent" if is_parent else "staff",
        requested_by_type="parent" if is_parent else "staff",
        requested_by_id=_actor_id(actor),
        expected_arrival_at=expected_arrival_at,
        status="arrived" if arrived else "pending",
        arrived_at=now if arrived else None,
        homework_status_at_request=reply.homework_status,
        reply_ready_eta=reply.reply_ready_eta,
        reply_message=reply.reply_message,
        reply_source=reply.reply_source,
        replied_at=now if reply.reply_source is not None else None,
    )
    try:
        with session.begin_nested():
            session.add(request)
            session.flush()
    except IntegrityError as exc:
        diag = getattr(exc.orig, "diag", None)
        if getattr(diag, "constraint_name", None) != UQ_ONE_OPEN:
            raise
        existing = session.execute(
            select(PickupRequest.id, PickupRequest.status).where(
                PickupRequest.student_id == student.id,
                PickupRequest.service_date == d,
                PickupRequest.status.in_(OPEN_STATUSES),
            )
        ).one()
        raise ConflictError(
            "pickup_request_exists",
            "今天已有進行中的接送請求",
            details={"request_id": existing.id, "status": existing.status},
        ) from exc

    operators = _operators(session)
    requested: dict[str, Any] = {
        "student_id": student.id,
        "student_name": student.name,
        "request_id": request.id,
    }
    if expected_arrival_at is not None:
        requested["expected_arrival_at"] = _taipei_hm(expected_arrival_at)
    enqueue(session, Event.PICKUP_REQUESTED, recipients=operators, payload=requested, clock=clock)
    if arrived:
        enqueue(
            session,
            Event.PICKUP_ARRIVED,
            recipients=operators,
            payload={
                "student_id": student.id,
                "student_name": student.name,
                "request_id": request.id,
            },
            clock=clock,
        )
    publish_request_change(session, request, clock=clock)
    return request


def _notify_requesting_parent(
    session: Session, request: PickupRequest, event: Event, payload: dict[str, Any], *, clock: Clock
) -> None:
    """員工代建的請求沒有發起家長，不通知。"""
    if request.requested_by_type != "parent":
        return
    enqueue(
        session,
        event,
        recipients=[Recipient("parent", request.requested_by_id)],
        payload={
            "student_id": request.student_id,
            "student_name": _student_name(session, request.student_id),
            "request_id": request.id,
            **payload,
        },
        clock=clock,
    )


def reply_request(
    session: Session,
    request_id: UUID,
    data: PickupReplyIn,
    *,
    actor: CurrentStaff,
    clock: Clock,
) -> PickupRequest:
    """員工覆寫回覆（reply_source=staff，狀態不變）；ETA 同交易寫回作業進度並推作業看板。

    不發 homework.eta_updated（家長已收到 pickup.replied），也不呼叫 sync_open_request_reply。
    """
    # 延遲 import：homework_service 也 import 本模組（sync_open_request_reply）
    from app.services.homework_service import broadcast_homework_snapshot, lock_progress_row

    now = clock.now()
    eta = _parse_hm(data.reply_ready_eta) if data.reply_ready_eta is not None else None
    message = data.reply_message or f"預計 {data.reply_ready_eta} 可接送"
    progress = None
    if eta is not None:
        # 鎖序一律「進度列 → 請求列」（與 BACKEND-376 / 382 / 413 一致），否則與作業模組互等成死結：
        # 先以不上鎖的查詢取得學生與日期、鎖進度列，再做請求列的條件式 UPDATE
        target = session.execute(
            select(PickupRequest.student_id, PickupRequest.service_date).where(
                PickupRequest.id == request_id
            )
        ).one_or_none()
        if target is not None:
            progress = lock_progress_row(session, target.student_id, target.service_date)
    request = transition_request(
        session,
        request_id,
        from_statuses=OPEN_STATUSES,
        to_status=None,
        values={
            "reply_ready_eta": eta,
            "reply_message": message,
            "reply_source": "staff",
            "replied_by": actor.id,
            "replied_at": now,
        },
    )
    if progress is not None:
        if progress.ready_eta != eta:
            progress.ready_eta = eta
            progress.eta_updated_by = actor.id
            progress.eta_updated_at = now
            session.flush()
        broadcast_homework_snapshot(session, request.student_id, request.service_date, clock=clock)
    _notify_requesting_parent(
        session, request, Event.PICKUP_REPLIED, {"reply_message": message}, clock=clock
    )
    publish_request_change(session, request, clock=clock)
    return request


def acknowledge_request(
    session: Session, request_id: UUID, *, actor: CurrentStaff, clock: Clock
) -> PickupRequest:
    """pending → acknowledged；家長尚未收過員工回覆時發 pickup.replied（目前回覆或預設文案）。

    移植 ivy ``api/portal/dismissal_calls.py::_db_acknowledge``；去掉教師端班級限制。
    """
    request = transition_request(
        session, request_id, from_statuses={"pending"}, to_status="acknowledged", values={}
    )
    if request.reply_source != "staff":
        _notify_requesting_parent(
            session,
            request,
            Event.PICKUP_REPLIED,
            {"reply_message": request.reply_message or ACK_DEFAULT_MESSAGE},
            clock=clock,
        )
    publish_request_change(session, request, clock=clock)
    return request


def mark_arrived(
    session: Session, request_id: UUID, *, parent: CurrentParent, clock: Clock
) -> PickupRequest:
    """家長「我到了」：pending / acknowledged → arrived；員工收到 pickup.arrived（ws transient）。

    同一小孩的任一綁定家長都可按；他人小孩的請求 404。移植 ivy ``arrive_dismissal_notice``。
    """
    request = transition_request(
        session,
        request_id,
        from_statuses={"pending", "acknowledged"},
        to_status="arrived",
        values={"arrived_at": clock.now()},
        student_ids=get_parent_student_ids(session, parent.id),
    )
    enqueue(
        session,
        Event.PICKUP_ARRIVED,
        recipients=_operators(session),
        payload={
            "student_id": request.student_id,
            "student_name": _student_name(session, request.student_id),
            "request_id": request.id,
        },
        clock=clock,
    )
    publish_request_change(session, request, clock=clock)
    return request


def cancel_request(
    session: Session,
    request_id: UUID,
    data: PickupCancelIn | None,
    *,
    actor: Actor,
    clock: Clock,
) -> PickupRequest:
    """非終態 → cancelled。家長只能取消自己小孩的請求（他人 404），員工可取消任何請求。

    家長取消 → 通知 pickup:operate 員工；員工取消家長發起的請求 → 通知發起家長。移植 ivy
    ``cancel_dismissal_notice`` 與員工端 ``_db_cancel_dismissal_call``。
    """
    reason = data.reason if data is not None else None
    is_parent = actor.type == "parent"
    student_ids = None
    if is_parent:
        student_ids = get_parent_student_ids(session, _actor_id(actor))
    request = transition_request(
        session,
        request_id,
        from_statuses=OPEN_STATUSES,
        to_status="cancelled",
        values={"cancelled_at": clock.now(), "cancel_reason": reason},
        student_ids=student_ids,
    )
    extra: dict[str, Any] = {"reason": reason} if reason else {}
    if is_parent:
        enqueue(
            session,
            Event.PICKUP_CANCELLED,
            recipients=_operators(session),
            payload={
                "student_id": request.student_id,
                "student_name": _student_name(session, request.student_id),
                "request_id": request.id,
                "cancelled_by_label": "家長",
                **extra,
            },
            clock=clock,
        )
    else:
        _notify_requesting_parent(
            session,
            request,
            Event.PICKUP_CANCELLED,
            {"cancelled_by_label": "老師", **extra},
            clock=clock,
        )
    publish_request_change(session, request, clock=clock)
    return request


def complete_request(
    session: Session,
    request_id: UUID,
    data: PickupCompleteIn,
    *,
    actor: CurrentStaff,
    meta: RequestMeta,
    clock: Clock,
) -> PickupRequest:
    """員工交付學生 → completed：記錄由哪位監護人接走（或主管強制完成），出勤改 left、通知家長。

    員工可從 pending / acknowledged / arrived 直接完成（家長到場但沒按「我到了」也能交付）。兩位
    員工同時完成時條件式 UPDATE 只讓一個成功，通知、出勤與稽核只發生一次。移植 ivy
    ``api/portal/dismissal_calls.py::_db_complete``；拿掉「家長尚未抵達不可完成」的限制。
    """
    current = session.execute(
        select(PickupRequest.student_id, PickupRequest.service_date, PickupRequest.status).where(
            PickupRequest.id == request_id
        )
    ).one_or_none()
    if current is None:
        raise NotFoundError("pickup_request_not_found", "找不到接送請求")

    guardian = None
    if data.method == "guardian":
        guardian = session.get(Guardian, data.guardian_id)
        if (
            guardian is None
            or guardian.student_id != current.student_id
            or guardian.archived_at is not None
        ):
            raise AppError("invalid_guardian", "監護人不屬於此學生", status=422)
        if not guardian.can_pickup:
            raise ConflictError("guardian_cannot_pickup", "此監護人未被設定為可接送")
    elif not actor.has(Permission.PICKUP_OVERRIDE):
        raise ForbiddenError(details={"required": [str(Permission.PICKUP_OVERRIDE)]})

    now = clock.now()
    request = transition_request(
        session,
        request_id,
        from_statuses=OPEN_STATUSES,
        to_status="completed",
        values={
            "completed_at": now,
            "completed_by": actor.id,
            "completion_method": data.method,
            # override 忽略 guardian_id：不寫入未經驗證的監護人
            "picked_up_by_guardian_id": guardian.id if guardian is not None else None,
            "arrived_at": func.coalesce(PickupRequest.arrived_at, now),
        },
    )
    if data.method == "override":
        record(
            session,
            actor=Actor.staff(actor),
            action="pickup.override_complete",
            entity_type="pickup_request",
            entity_id=request.id,
            before={"status": current.status},
            after={"status": "completed", "note": data.note},
            meta=meta,
        )
    mark_left_by_pickup(session, request.student_id, request.service_date, at=now, clock=clock)
    enqueue(
        session,
        Event.PICKUP_COMPLETED,
        recipients=parent_recipients(session, request.student_id),
        payload={
            "student_id": request.student_id,
            "student_name": _student_name(session, request.student_id),
            "request_id": request.id,
            "time": _taipei_hm(now),
            "picked_up_by": guardian.name if guardian is not None else OVERRIDE_PICKER_NAME,
        },
        clock=clock,
    )
    publish_request_change(session, request, clock=clock)
    return request
