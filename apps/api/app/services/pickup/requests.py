"""接送請求查詢與回覆同步（domain_spec M7）。

- BACKEND-414：``get_queue``（後台接送佇列）。移植 ivy
  ``api/dismissal_calls.py::list_dismissal_calls`` 的「只看當日」；去掉班級篩選。
- BACKEND-415：``get_roster``（接送 POS 學生卡）。移植 ivy
  ``services/dismissal_pos.py::get_pos_status`` 的「一次查出當日請假名單」；去掉娃娃車、tenant、
  POS 標記請假。
- BACKEND-416：``list_today_requests_for_parent``（家長端今日請求）。移植 ivy
  ``api/parent_portal/dismissal_calls.py::list_dismissal_notices``。
- BACKEND-413：``sync_open_request_reply``（作業完成或 ETA 變動時同步進行中請求的回覆）。
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, time
from typing import Any, Final
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.clock import Clock, to_taipei
from app.core.settings_registry import HOMEWORK_DEFAULTS
from app.models.attendance import StudentAttendance
from app.models.classes import SchoolClass
from app.models.homework import HomeworkDailyProgress
from app.models.leaves import StudentLeave
from app.models.pickup import OPEN_STATUSES, PickupAuthorization, PickupRequest
from app.models.students import Student
from app.schemas.pickup import (
    ParentPickupRequestOut,
    PickupQueueCountsOut,
    PickupQueueOut,
    PickupQueueQuery,
    RosterClassOut,
    RosterOpenRequestOut,
    RosterOut,
    RosterQuery,
    RosterStudentOut,
)
from app.services.parent_scope import get_parent_student_ids
from app.services.pickup.auto_reply import DONE_REPLY_TEXT, ProgressSnapshot, compute_auto_reply
from app.services.pickup.views import (
    build_parent_request_views,
    build_request_views,
    needs_reply,
    publish_request_change,
)
from app.services.settings_service import get_setting

UNASSIGNED_CLASS_NAME: Final = "未分班"
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
