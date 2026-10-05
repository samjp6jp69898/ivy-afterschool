"""BACKEND-525：家長端小孩今日狀態聚合（domain_spec §4 家長端首頁：今日狀態卡）。

呼叫端已以 ``get_owned_student`` 驗證所有權（BACKEND-526）。不回傳員工姓名與內部備註（出勤 note、
updated_by 等）。SQL 固定（≤ 6）：營業日判斷（設定快取未命中時 2 條）、出勤、請假、作業（項目計數與
進度合併一條）、接送請求（接走人名稱以 outer join 一併取得）。
"""

from __future__ import annotations

from datetime import datetime, time
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.core.clock import Clock, to_taipei
from app.models.attendance import StudentAttendance
from app.models.homework import HomeworkDailyProgress, HomeworkItem
from app.models.leaves import LEAVE_TYPE_LABELS, StudentLeave
from app.models.parents import Guardian
from app.models.pickup import OPEN_STATUSES, PickupAuthorization, PickupRequest
from app.schemas.parent_children import (
    ChildTodayOut,
    TodayAttendanceOut,
    TodayHomeworkOut,
    TodayLeaveOut,
    TodayPickupRequestOut,
)
from app.services.pickup.views import CAN_MARK_ARRIVED_STATUSES, OVERRIDE_PICKER_NAME
from app.services.service_calendar import is_service_day


def _hm(value: time | None) -> str | None:
    return value.strftime("%H:%M") if value is not None else None


def _taipei_hm(value: datetime | None) -> str | None:
    return to_taipei(value).strftime("%H:%M") if value is not None else None


def get_child_today(session: Session, student_id: UUID, *, clock: Clock) -> ChildTodayOut:
    d = clock.today()
    service_day = is_service_day(session, d)

    row = session.execute(
        select(
            StudentAttendance.status,
            StudentAttendance.check_in_at,
            StudentAttendance.check_out_at,
        ).where(StudentAttendance.student_id == student_id, StudentAttendance.service_date == d)
    ).one_or_none()
    if row is not None:
        attendance = TodayAttendanceOut(
            status=row.status, check_in_at=row.check_in_at, check_out_at=row.check_out_at
        )
    else:
        # 與後台虛擬列一致：營業日尚無出勤列視為 expected，非營業日沒有狀態
        attendance = TodayAttendanceOut(
            status="expected" if service_day else None, check_in_at=None, check_out_at=None
        )

    # 不依賴出勤列：涵蓋今天的 active 請假（DB exclusion constraint 保證至多一筆）
    leave = session.execute(
        select(
            StudentLeave.id, StudentLeave.leave_type, StudentLeave.start_date, StudentLeave.end_date
        ).where(
            StudentLeave.student_id == student_id,
            StudentLeave.status == "active",
            StudentLeave.start_date <= d,
            StudentLeave.end_date >= d,
        )
    ).one_or_none()

    items_of_day = (HomeworkItem.student_id == student_id, HomeworkItem.service_date == d)
    progress_of_day = (
        HomeworkDailyProgress.student_id == student_id,
        HomeworkDailyProgress.service_date == d,
    )
    item_count, done_count, overall, eta, note = session.execute(
        select(
            select(func.count()).where(*items_of_day).scalar_subquery(),
            select(func.count())
            .where(*items_of_day, HomeworkItem.status == "done")
            .scalar_subquery(),
            select(HomeworkDailyProgress.overall_status).where(*progress_of_day).scalar_subquery(),
            select(HomeworkDailyProgress.ready_eta).where(*progress_of_day).scalar_subquery(),
            select(HomeworkDailyProgress.note).where(*progress_of_day).scalar_subquery(),
        )
    ).one()

    # 非終態那筆優先（uq_pickup_requests_one_open 保證至多一筆），否則取今日最新的終態請求
    pickup = session.execute(
        select(PickupRequest, Guardian.name, PickupAuthorization.proxy_name)
        .outerjoin(Guardian, Guardian.id == PickupRequest.picked_up_by_guardian_id)
        .outerjoin(
            PickupAuthorization,
            PickupAuthorization.id == PickupRequest.picked_up_by_authorization_id,
        )
        .where(PickupRequest.student_id == student_id, PickupRequest.service_date == d)
        .order_by(
            case((PickupRequest.status.in_(OPEN_STATUSES), 0), else_=1),
            PickupRequest.created_at.desc(),
            PickupRequest.id,
        )
        .limit(1)
    ).one_or_none()

    return ChildTodayOut(
        student_id=student_id,
        date=d,
        is_service_day=service_day,
        attendance=attendance,
        on_leave=leave is not None,
        leave=(
            TodayLeaveOut(
                id=leave.id,
                leave_type=leave.leave_type,
                leave_type_label=LEAVE_TYPE_LABELS[leave.leave_type],
                start_date=leave.start_date,
                end_date=leave.end_date,
            )
            if leave is not None
            else None
        ),
        homework=TodayHomeworkOut(
            item_count=item_count,
            done_count=done_count,
            overall_status=overall or "not_started",
            ready_eta=_hm(eta),
            note=note,
        ),
        pickup_request=_pickup_out(*pickup) if pickup is not None else None,
    )


def _pickup_out(
    request: PickupRequest, guardian_name: str | None, proxy_name: str | None
) -> TodayPickupRequestOut:
    """接走人名稱規則同 BACKEND-404 家長版（監護人、代理人，或強制完成時「老師確認交付」）。"""
    picked_up_by = guardian_name or proxy_name
    if picked_up_by is None and request.completion_method == "override":
        picked_up_by = OVERRIDE_PICKER_NAME
    return TodayPickupRequestOut(
        id=request.id,
        status=request.status,
        expected_arrival_at=_taipei_hm(request.expected_arrival_at),
        reply_ready_eta=_hm(request.reply_ready_eta),
        reply_message=request.reply_message,
        reply_source=request.reply_source,
        completed_at=request.completed_at,
        picked_up_by_name=picked_up_by,
        can_cancel=request.status in OPEN_STATUSES,
        can_mark_arrived=request.status in CAN_MARK_ARRIVED_STATUSES,
    )
