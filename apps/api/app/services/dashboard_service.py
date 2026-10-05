"""儀表板 service（domain_spec M11：後台首頁今日聚合）。

- BACKEND-491：``get_today_dashboard``。參考 ivy
  ``services/dashboard_query_service.py::DashboardQueryService._compute_student_attendance_summary``
  的單次聚合計數；去掉幼稚園的審核、用藥、活動等區塊。
"""

from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
from typing import Final

from sqlalchemy import Executable, and_, exists, func, literal, or_, select, union_all
from sqlalchemy.orm import Session

from app.core.clock import Clock
from app.models.attendance import StudentAttendance
from app.models.classes import SchoolClass
from app.models.homework import HomeworkDailyProgress
from app.models.leaves import LEAVE_TYPE_LABELS, StudentLeave
from app.models.pickup import OPEN_STATUSES, PickupRequest
from app.models.students import Student
from app.schemas.dashboard import (
    AttendanceCountsOut,
    DashboardTodayOut,
    HomeworkCountsOut,
    PickupCountsOut,
    RecentLeaveOut,
)
from app.services.service_calendar import is_service_day

RECENT_LEAVE_DAYS: Final = 7
RECENT_LEAVE_LIMIT: Final = 10
# 營業日尚無出勤列的 active 學生，在出勤計數查詢中以此標記與實際 status 一起回傳
_MISSING: Final = "_missing"


def _attendance_counts(session: Session, d: date, service_day: bool) -> AttendanceCountsOut:
    """實際列 GROUP BY status；營業日另以 UNION ALL 併入尚無列的 active 學生數（一條 SQL）。"""
    by_status = (
        select(StudentAttendance.status, func.count())
        .where(StudentAttendance.service_date == d)
        .group_by(StudentAttendance.status)
    )
    stmt: Executable = by_status
    if service_day:
        has_row = exists().where(
            StudentAttendance.student_id == Student.id, StudentAttendance.service_date == d
        )
        missing = select(literal(_MISSING), func.count()).where(
            Student.status == "active",
            Student.archived_at.is_(None),
            # 尚未入班者不算應到（與 BACKEND-303 / 311 一致）
            or_(Student.enrolled_on.is_(None), Student.enrolled_on <= d),
            ~has_row,
        )
        stmt = union_all(by_status, missing)
    counts: Counter[str] = Counter({status: n for status, n in session.execute(stmt)})
    not_arrived = counts["expected"] + counts[_MISSING]
    arrived = counts["present"] + counts["left"]
    return AttendanceCountsOut(
        expected_total=arrived + not_arrived + counts["absent"],
        arrived=arrived,
        present=counts["present"],
        left=counts["left"],
        not_arrived=not_arrived,
        leave=counts["leave"],
        absent=counts["absent"],
    )


def _pickup_counts(session: Session, d: date) -> PickupCountsOut:
    """needs_reply = 非終態且（尚未回覆，或自動回覆沒有 ETA 且當日作業未完成）。"""
    homework_done = exists().where(
        HomeworkDailyProgress.student_id == PickupRequest.student_id,
        HomeworkDailyProgress.service_date == d,
        HomeworkDailyProgress.overall_status == "done",
    )
    needs_reply = and_(
        PickupRequest.status.in_(OPEN_STATUSES),
        or_(
            PickupRequest.reply_source.is_(None),
            and_(
                PickupRequest.reply_source == "auto",
                PickupRequest.reply_ready_eta.is_(None),
                ~homework_done,
            ),
        ),
    )
    rows = session.execute(
        select(
            PickupRequest.status,
            func.count(),
            func.count().filter(needs_reply),
        )
        .where(PickupRequest.service_date == d)
        .group_by(PickupRequest.status)
    ).all()
    by_status: dict[str, int] = {status: n for status, n, _ in rows}
    return PickupCountsOut(
        open=sum(by_status.get(status, 0) for status in OPEN_STATUSES),
        needs_reply=sum(n for _, _, n in rows),
        arrived=by_status.get("arrived", 0),
        completed=by_status.get("completed", 0),
    )


def _homework_counts(session: Session, d: date) -> HomeworkCountsOut:
    """今日 present / left 學生的作業整體狀態（無進度列視為 not_started）。"""
    overall = func.coalesce(HomeworkDailyProgress.overall_status, "not_started")
    rows = session.execute(
        select(overall, func.count())
        .select_from(StudentAttendance)
        .outerjoin(
            HomeworkDailyProgress,
            and_(
                HomeworkDailyProgress.student_id == StudentAttendance.student_id,
                HomeworkDailyProgress.service_date == d,
            ),
        )
        .where(
            StudentAttendance.service_date == d,
            StudentAttendance.status.in_(("present", "left")),
        )
        .group_by(overall)
    )
    counts: Counter[str] = Counter({status: n for status, n in rows})
    total = counts.total()
    return HomeworkCountsOut(
        total=total,
        done=counts["done"],
        in_progress=counts["in_progress"],
        not_started=counts["not_started"],
        # float 交給 schema 四捨五入到 1 位（Decimal 會原樣輸出）
        completion_rate=counts["done"] / total * 100 if total else 0.0,
    )


def _recent_leaves(session: Session, d: date) -> list[RecentLeaveOut]:
    # 只取欄位：載入 StudentLeave entity 會觸發 attachments 的 selectin 查詢
    rows = session.execute(
        select(
            StudentLeave.id,
            StudentLeave.student_id,
            Student.name,
            SchoolClass.name,
            StudentLeave.leave_type,
            StudentLeave.start_date,
            StudentLeave.end_date,
            StudentLeave.created_by_type,
            StudentLeave.created_at,
        )
        .join(Student, Student.id == StudentLeave.student_id)
        .outerjoin(SchoolClass, SchoolClass.id == Student.class_id)
        .where(
            StudentLeave.status == "active",
            StudentLeave.end_date >= d,
            StudentLeave.start_date <= d + timedelta(days=RECENT_LEAVE_DAYS),
        )
        .order_by(StudentLeave.start_date, StudentLeave.created_at, StudentLeave.id)
        .limit(RECENT_LEAVE_LIMIT)
    )
    return [
        RecentLeaveOut(
            id=leave_id,
            student_id=student_id,
            student_name=student_name,
            class_name=class_name,
            leave_type_label=LEAVE_TYPE_LABELS[leave_type],
            start_date=start_date,
            end_date=end_date,
            created_by_type=created_by_type,
            created_at=created_at,
        )
        for (
            leave_id,
            student_id,
            student_name,
            class_name,
            leave_type,
            start_date,
            end_date,
            created_by_type,
            created_at,
        ) in rows
    ]


def get_today_dashboard(session: Session, *, clock: Clock) -> DashboardTodayOut:
    """全部聚合 ≤ 6 條 SQL（不隨學生數成長）：營業日判斷（設定快取未命中時 2 條）、出勤、接送、
    作業、近期請假各 1 條。"""
    d = clock.today()
    service_day = is_service_day(session, d)
    return DashboardTodayOut(
        date=d,
        is_service_day=service_day,
        attendance=_attendance_counts(session, d, service_day),
        pickup=_pickup_counts(session, d),
        homework=_homework_counts(session, d),
        recent_leaves=_recent_leaves(session, d),
    )
