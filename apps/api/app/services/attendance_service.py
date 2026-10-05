"""出勤 service（domain_spec M4）。

- BACKEND-302：``ensure_attendance_row``（取得或建立單一學生某日出勤列，check_in / mark_absent /
  batch_check_in 共用）。
- BACKEND-310：``amend_attendance``（改判已登記出勤，寫 audit，commit 後推播 admin / 家長）。
- BACKEND-311：``get_daily_attendance``（每日出勤清單與統計；營業日尚無列的 active 學生以虛擬列
  呈現）。
- BACKEND-312：``get_monthly_attendance``（月出勤報表；入班前與未來日期不計）。
- BACKEND-303：``initialize_daily_attendance``（營業日為在學學生建立出勤列，冪等）。
- BACKEND-305 / 309：``check_in`` / ``mark_absent``（條件式更新，防兩位員工同時操作）。
- BACKEND-314：``get_child_monthly_attendance``（家長端單一小孩月出勤，不含內部欄位）。
- BACKEND-306 / 307：``check_out`` / ``mark_left_by_pickup``（條件式更新；接送完成來源為 pickup）。
- BACKEND-308：``batch_check_in``（逐生條件式更新、回報略過原因、只廣播一次）。
"""

from __future__ import annotations

import calendar
import logging
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any, Final
from uuid import UUID

from sqlalchemy import Select, exists, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.clock import Clock, to_taipei
from app.core.errors import AppError, ConflictError, NotFoundError
from app.models.attendance import AttendanceStatus, StudentAttendance
from app.models.classes import SchoolClass
from app.models.leaves import LEAVE_TYPE_LABELS, StudentLeave
from app.models.students import Student
from app.notifications.events import Event
from app.notifications.recipients import parent_recipients, parent_recipients_bulk
from app.notifications.service import enqueue
from app.realtime.publish import broadcast_after_commit, push_to_student_after_commit
from app.repositories.students import (
    StudentBrief,
    get_student_or_404,
    list_active_student_ids,
    student_brief_map,
)
from app.schemas.attendance import (
    AttendanceAmendIn,
    AttendanceRowOut,
    BatchCheckInOut,
    BatchSkipOut,
    DailyAttendanceOut,
    DailyAttendanceQuery,
    DailySummaryOut,
    LeaveBriefOut,
    MonthDayOut,
    MonthlyAttendanceOut,
    MonthlyAttendanceQuery,
    MonthlyStatsOut,
    MonthlyStudentRowOut,
    ParentAttendanceDayOut,
    ParentMonthlyAttendanceOut,
    to_parent_attendance_event,
)
from app.services.audit_service import Actor, record
from app.services.service_calendar import is_service_day, list_service_days

if TYPE_CHECKING:
    from app.api.deps import CurrentStaff
    from app.core.request_meta import RequestMeta

logger = logging.getLogger(__name__)

# 與 schemas.attendance.MONTH_PATTERN 相同；家長端 month 為 path / query 字串，service 自行檢查
_MONTH_RE: Final = re.compile(r"\d{4}-(0[1-9]|1[0-2])")


def _active_leave_id(session: Session, student_id: UUID, service_date: date) -> UUID | None:
    """涵蓋 service_date 的 active 請假（DB exclusion constraint 保證至多一筆）。"""
    return session.execute(
        select(StudentLeave.id).where(
            StudentLeave.student_id == student_id,
            StudentLeave.status == "active",
            StudentLeave.start_date <= service_date,
            StudentLeave.end_date >= service_date,
        )
    ).scalar_one_or_none()


def ensure_attendance_row(
    session: Session, student_id: UUID, service_date: date, *, for_update: bool = True
) -> StudentAttendance:
    """取得或建立出勤列（冪等）；無列時依請假建立 leave 或 expected，已有列原樣回傳。

    不檢查營業日與學生狀態（由呼叫端負責）；不 commit。兩個交易同時建立時，第二個 INSERT 等第一個
    交易結束後 do nothing，不會拋 IntegrityError；for_update 讓後續的狀態更新在此列上序列化。
    """
    leave_id = _active_leave_id(session, student_id, service_date)
    session.execute(
        pg_insert(StudentAttendance)
        .values(
            student_id=student_id,
            service_date=service_date,
            status="leave" if leave_id is not None else "expected",
            leave_id=leave_id,
        )
        .on_conflict_do_nothing(index_elements=["student_id", "service_date"])
    )
    stmt = select(StudentAttendance).where(
        StudentAttendance.student_id == student_id,
        StudentAttendance.service_date == service_date,
    )
    if for_update:
        stmt = stmt.with_for_update()
    # 已載入的物件以 DB 現值刷新（別的交易可能剛改過）
    return session.execute(stmt.execution_options(populate_existing=True)).scalar_one()


def _leave_columns() -> tuple[Any, ...]:
    # 只取欄位：載入 StudentLeave entity 會觸發 attachments 的 selectin 查詢
    return (
        StudentLeave.id,
        StudentLeave.leave_type,
        StudentLeave.start_date,
        StudentLeave.end_date,
    )


def _leave_brief(row: Sequence[Any]) -> LeaveBriefOut | None:
    leave_id, leave_type, start_date, end_date = row
    if leave_id is None:
        return None
    return LeaveBriefOut(
        id=leave_id,
        leave_type=leave_type,
        leave_type_label=LEAVE_TYPE_LABELS[leave_type],
        start_date=start_date,
        end_date=end_date,
    )


def _row_out(
    row: StudentAttendance, brief: StudentBrief, leave: LeaveBriefOut | None
) -> AttendanceRowOut:
    return AttendanceRowOut(
        id=row.id,
        student_id=row.student_id,
        student_no=brief.student_no,
        student_name=brief.name,
        grade_level=brief.grade_level,
        class_id=brief.class_id,
        class_name=brief.class_name,
        service_date=row.service_date,
        status=row.status,
        check_in_at=row.check_in_at,
        check_in_source=row.check_in_source,
        check_out_at=row.check_out_at,
        check_out_source=row.check_out_source,
        leave=leave,
        note=row.note,
        updated_at=row.updated_at,
    )


def _single_row_out(session: Session, row: StudentAttendance) -> AttendanceRowOut:
    brief = student_brief_map(session, [row.student_id])[row.student_id]
    leave = None
    if row.leave_id is not None:
        leave = _leave_brief(
            session.execute(select(*_leave_columns()).where(StudentLeave.id == row.leave_id)).one()
        )
    return _row_out(row, brief, leave)


def _on_service_date(value: datetime, service_date: date) -> bool:
    try:
        return to_taipei(value).date() == service_date
    except OverflowError:
        # 9999-12-31T23:00-08:00 這類極端值轉台北時間會溢位，必然不在 service_date
        return False


def _snapshot(row: StudentAttendance) -> dict[str, Any]:
    return {
        "status": row.status,
        "check_in_at": row.check_in_at,
        "check_out_at": row.check_out_at,
        "note": row.note,
    }


def amend_attendance(
    session: Session,
    attendance_id: UUID,
    data: AttendanceAmendIn,
    *,
    actor: CurrentStaff,
    meta: RequestMeta,
    clock: Clock,
) -> AttendanceRowOut:
    """改判已登記出勤（未給的欄位沿用原值）；請假列只能由請假的建立 / 取消改變。"""
    row = session.execute(
        select(StudentAttendance)
        .where(StudentAttendance.id == attendance_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError("attendance_not_found", "找不到出勤紀錄")
    if row.status == "leave":
        raise ConflictError("attendance_managed_by_leave", "請假中的出勤只能由請假的建立或取消改變")

    given = data.model_fields_set
    # status=null 與其他欄位一起送時仍在 fields_set 內：視為未提供
    status = data.status if data.status is not None else row.status
    check_in_at = data.check_in_at if "check_in_at" in given else row.check_in_at
    check_out_at = data.check_out_at if "check_out_at" in given else row.check_out_at
    note = data.note if "note" in given else row.note

    if status in ("expected", "absent"):
        check_in_at = check_out_at = None
    elif status == "present":
        if check_in_at is None:
            raise AppError("check_in_required", "已到班需要到班時間", status=422)
        check_out_at = None
    elif check_in_at is None or check_out_at is None:
        raise AppError("check_out_required", "已離班需要到班與離班時間", status=422)

    if check_in_at is not None and check_out_at is not None and check_out_at < check_in_at:
        raise AppError("invalid_times", "離班時間不可早於到班時間", status=422)
    for value in (check_in_at, check_out_at):
        if value is not None and not _on_service_date(value, row.service_date):
            raise AppError("time_not_on_service_date", "時間必須在該出勤日當天", status=422)

    before = _snapshot(row)
    after = {
        "status": status,
        "check_in_at": check_in_at,
        "check_out_at": check_out_at,
        "note": note,
    }
    if after == before:
        raise AppError("no_changes", "內容與原紀錄相同", status=422)

    # 時間有變動的那一側 source 設為 manual；未變動者保留原 source，清空時一併清空
    if check_in_at != row.check_in_at:
        row.check_in_source = "manual" if check_in_at is not None else None
    if check_out_at != row.check_out_at:
        row.check_out_source = "manual" if check_out_at is not None else None
    row.status = status
    row.check_in_at = check_in_at
    row.check_out_at = check_out_at
    row.note = note
    row.updated_by = actor.id
    session.flush()

    record(
        session,
        actor=Actor.staff(actor),
        action="attendance.amend",
        entity_type="student_attendance",
        entity_id=row.id,
        before=before,
        after={**after, "reason": data.reason},
        meta=meta,
    )
    return _publish_row(session, row, clock=clock)


def _publish_row(session: Session, row: StudentAttendance, *, clock: Clock) -> AttendanceRowOut:
    """commit 後推 admin:attendance 完整列與 student:<id> 家長裁切資料（BACKEND-224）。"""
    out = _single_row_out(session, row)
    broadcast_after_commit(
        session,
        topic="attendance",
        type="attendance.updated",
        data=out.model_dump(),
        student_id=row.student_id,
        parent_data=to_parent_attendance_event(row),
        clock=clock,
    )
    return out


def _student_columns() -> tuple[Any, ...]:
    return (
        Student.id,
        Student.student_no,
        Student.name,
        Student.grade_level,
        Student.class_id,
        SchoolClass.name,
        Student.status,
        SchoolClass.sort_order,
    )


def _brief(row: Sequence[Any]) -> StudentBrief:
    return StudentBrief(
        id=row[0],
        student_no=row[1],
        name=row[2],
        grade_level=row[3],
        class_id=row[4],
        class_name=row[5],
        status=row[6],
    )


type _SortKey = tuple[bool, int, str, str]


def _sort_key(brief: StudentBrief, class_sort_order: int | None) -> _SortKey:
    """班級 sort_order、班名、學號；沒有班級的學生排最後。"""
    return (brief.class_id is None, class_sort_order or 0, brief.class_name or "", brief.student_no)


def _virtual_row(brief: StudentBrief, service_date: date) -> AttendanceRowOut:
    return AttendanceRowOut(
        id=None,
        student_id=brief.id,
        student_no=brief.student_no,
        student_name=brief.name,
        grade_level=brief.grade_level,
        class_id=brief.class_id,
        class_name=brief.class_name,
        service_date=service_date,
        status="expected",
        check_in_at=None,
        check_in_source=None,
        check_out_at=None,
        check_out_source=None,
        leave=None,
        note=None,
        updated_at=None,
    )


def _with_class_filter[*Ts](stmt: Select[*Ts], class_id: UUID | None) -> Select[*Ts]:
    return stmt.where(Student.class_id == class_id) if class_id is not None else stmt


def get_daily_attendance(
    session: Session, query: DailyAttendanceQuery, *, clock: Clock
) -> DailyAttendanceOut:
    """學生集合 = 當天已有出勤列的學生（含已非 active）與營業日中 active 未封存的學生（虛擬列）。

    status 篩選在組完虛擬列後套用，summary 以篩選前的全部列計算。SQL 固定：營業日判斷（設定快取
    未命中時多一條）、當天出勤列（join 學生 / 班級 / 請假）、尚無列的 active 學生。
    """
    d = query.date or clock.today()
    service_day = is_service_day(session, d)

    actual = session.execute(
        _with_class_filter(
            select(StudentAttendance, *_leave_columns(), *_student_columns())
            .join(Student, Student.id == StudentAttendance.student_id)
            .outerjoin(SchoolClass, SchoolClass.id == Student.class_id)
            .outerjoin(StudentLeave, StudentLeave.id == StudentAttendance.leave_id)
            .where(StudentAttendance.service_date == d),
            query.class_id,
        )
    ).all()
    keyed: list[tuple[_SortKey, AttendanceRowOut]] = []
    for row in actual:
        student = row[5:]
        brief = _brief(student)
        keyed.append(
            (_sort_key(brief, student[7]), _row_out(row[0], brief, _leave_brief(row[1:5])))
        )

    if service_day:
        has_row = exists().where(
            StudentAttendance.student_id == Student.id, StudentAttendance.service_date == d
        )
        missing = session.execute(
            _with_class_filter(
                select(*_student_columns())
                .outerjoin(SchoolClass, SchoolClass.id == Student.class_id)
                .where(
                    Student.status == "active",
                    Student.archived_at.is_(None),
                    # 尚未入班者不產生虛擬列（與 BACKEND-303 建列範圍一致）
                    or_(Student.enrolled_on.is_(None), Student.enrolled_on <= d),
                    ~has_row,
                ),
                query.class_id,
            )
        ).all()
        for student in missing:
            brief = _brief(student)
            keyed.append((_sort_key(brief, student[7]), _virtual_row(brief, d)))

    keyed.sort(key=lambda pair: pair[0])
    items = [item for _, item in keyed]
    counts = Counter(item.status for item in items)
    summary = DailySummaryOut(
        total=len(items),
        expected=counts["expected"],
        present=counts["present"],
        left=counts["left"],
        absent=counts["absent"],
        leave=counts["leave"],
    )
    if query.status is not None:
        items = [item for item in items if item.status == query.status]
    return DailyAttendanceOut(date=d, is_service_day=service_day, summary=summary, items=items)


def _parse_month(month: str) -> tuple[date, date]:
    """schema 的 \\d 也接受全形數字與年份 0000：int() 後再以 date 檢查範圍。"""
    if not _MONTH_RE.fullmatch(month):
        raise AppError("invalid_month", "月份格式不正確", status=422)
    year_text, month_text = month.split("-")
    try:
        year, month_no = int(year_text), int(month_text)
        start = date(year, month_no, 1)
    except ValueError as exc:
        raise AppError("invalid_month", "月份格式不正確", status=422) from exc
    return start, start.replace(day=calendar.monthrange(year, month_no)[1])


def _monthly_stats(statuses: list[AttendanceStatus | None], counted: list[bool]) -> MonthlyStatsOut:
    counts = Counter(s for s, ok in zip(statuses, counted, strict=True) if ok)
    return MonthlyStatsOut(
        service_days=sum(counted),
        attended=counts["present"] + counts["left"],
        absent=counts["absent"],
        leave=counts["leave"],
        unrecorded=counts["expected"] + counts[None],
    )


def get_monthly_attendance(
    session: Session, query: MonthlyAttendanceQuery, *, clock: Clock
) -> MonthlyAttendanceOut:
    """days 為整月每一天；學生 = 該月有出勤列的學生與目前 active 未封存的學生（依目前班級篩選）。

    stats 只計 enrolled_on 之後、且不晚於今天的營業日。SQL 固定：營業日（設定快取未命中時多一條
    + closed_days）、學生（join 班級）、整月出勤列。
    """
    month_start, month_end = _parse_month(query.month)
    today = clock.today()
    service_days = set(list_service_days(session, month_start, month_end))
    dates = [
        month_start + timedelta(days=offset) for offset in range((month_end - month_start).days + 1)
    ]

    has_row_in_month = exists().where(
        StudentAttendance.student_id == Student.id,
        StudentAttendance.service_date.between(month_start, month_end),
    )
    students = session.execute(
        _with_class_filter(
            select(
                Student.id, Student.student_no, Student.name, Student.enrolled_on, SchoolClass.name
            )
            .outerjoin(SchoolClass, SchoolClass.id == Student.class_id)
            .where(
                or_(
                    (Student.status == "active") & Student.archived_at.is_(None),
                    has_row_in_month,
                )
            )
            .order_by(Student.student_no, Student.id),
            query.class_id,
        )
    ).all()
    student_ids = [row[0] for row in students]
    status_of: dict[tuple[UUID, date], AttendanceStatus] = {}
    if student_ids:
        status_of = {
            (student_id, service_date): status
            for student_id, service_date, status in session.execute(
                select(
                    StudentAttendance.student_id,
                    StudentAttendance.service_date,
                    StudentAttendance.status,
                ).where(
                    StudentAttendance.student_id.in_(student_ids),
                    StudentAttendance.service_date.between(month_start, month_end),
                )
            )
        }

    rows: list[MonthlyStudentRowOut] = []
    for student_id, student_no, name, enrolled_on, student_class_name in students:
        statuses = [status_of.get((student_id, d)) if d in service_days else None for d in dates]
        counted = [
            d in service_days and d <= today and (enrolled_on is None or d >= enrolled_on)
            for d in dates
        ]
        rows.append(
            MonthlyStudentRowOut(
                student_id=student_id,
                student_no=student_no,
                name=name,
                class_name=student_class_name,
                statuses=statuses,
                stats=_monthly_stats(statuses, counted),
            )
        )

    # 依班級篩選時學生的目前班級就是該班；班上沒有學生時才另查班名
    class_name: str | None = None
    if query.class_id is not None:
        class_name = (
            students[0][4]
            if students
            else session.execute(
                select(SchoolClass.name).where(SchoolClass.id == query.class_id)
            ).scalar_one_or_none()
        )
    return MonthlyAttendanceOut(
        month=f"{month_start.year:04d}-{month_start.month:02d}",
        class_id=query.class_id,
        class_name=class_name,
        days=[
            MonthDayOut(date=d, weekday=d.weekday(), is_service_day=d in service_days)
            for d in dates
        ],
        students=rows,
        totals=MonthlyStatsOut(
            service_days=sum(r.stats.service_days for r in rows),
            attended=sum(r.stats.attended for r in rows),
            absent=sum(r.stats.absent for r in rows),
            leave=sum(r.stats.leave for r in rows),
            unrecorded=sum(r.stats.unrecorded for r in rows),
        ),
    )


@dataclass(frozen=True)
class DailyInitResult:
    service_date: date
    skipped: bool  # 非營業日 → True，且不寫入任何列
    created_expected: int
    created_leave: int


def initialize_daily_attendance(
    session: Session, service_date: date, *, clock: Clock
) -> DailyInitResult:
    """營業日為 active 未封存、已入班的學生建立出勤列；當日有 active 請假者直接建 leave。

    單一 ``INSERT ... ON CONFLICT DO NOTHING RETURNING status``：已存在的列一律不動（員工已登記的
    到班 / 缺席不被覆蓋），同日重跑或與其他路徑同時寫入都不拋 unique 違反；以 RETURNING 只計本次
    真正插入的列。與請假建立交易同時進行時，INSERT 會等待對方未 commit 的列。不 commit（由
    BACKEND-304 的 job runner commit）。移植 ivy ``_student_active_on`` 的「入班日之前不計」。
    """
    if not is_service_day(session, service_date):
        return DailyInitResult(service_date, skipped=True, created_expected=0, created_leave=0)

    candidates = list_active_student_ids(session)
    if not candidates:
        return DailyInitResult(service_date, skipped=False, created_expected=0, created_leave=0)
    not_yet_enrolled = set(
        session.execute(
            select(Student.id).where(Student.id.in_(candidates), Student.enrolled_on > service_date)
        ).scalars()
    )
    student_ids = [sid for sid in candidates if sid not in not_yet_enrolled]
    leaves = {
        student_id: leave_id
        for student_id, leave_id in session.execute(
            select(StudentLeave.student_id, StudentLeave.id).where(
                StudentLeave.student_id.in_(student_ids),
                StudentLeave.status == "active",
                StudentLeave.start_date <= service_date,
                StudentLeave.end_date >= service_date,
            )
        )
    }
    if not student_ids:
        return DailyInitResult(service_date, skipped=False, created_expected=0, created_leave=0)
    inserted = session.execute(
        pg_insert(StudentAttendance)
        .values(
            [
                {
                    "student_id": student_id,
                    "service_date": service_date,
                    "status": "leave" if student_id in leaves else "expected",
                    "leave_id": leaves.get(student_id),
                }
                for student_id in student_ids
            ]
        )
        .on_conflict_do_nothing(index_elements=["student_id", "service_date"])
        .returning(StudentAttendance.status)
    ).scalars()
    counts = Counter(inserted)
    return DailyInitResult(
        service_date,
        skipped=False,
        created_expected=counts["expected"],
        created_leave=counts["leave"],
    )


def _require_active_on_service_day(session: Session, student_id: UUID, d: date) -> Student:
    student = get_student_or_404(session, student_id)
    if student.status != "active":
        raise ConflictError("student_not_active", "學生目前不在學，無法登記出勤")
    if not is_service_day(session, d):
        raise ConflictError("not_service_day", "今天不是營業日")
    return student


def _reload(session: Session, row_id: UUID) -> StudentAttendance:
    # 條件式 UPDATE 不經 ORM：以 DB 現值覆蓋已載入的物件
    return session.execute(
        select(StudentAttendance)
        .where(StudentAttendance.id == row_id)
        .execution_options(populate_existing=True)
    ).scalar_one()


_CHECK_IN_CONFLICTS: Final = {
    "already_checked_in": "學生今天已登記到班",
    "already_checked_out": "學生今天已離班",
    "student_on_leave": "學生今天請假",
}


def _try_check_in(
    session: Session,
    student_id: UUID,
    d: date,
    *,
    actor_id: UUID,
    note: str | None,
    now: datetime,
) -> tuple[StudentAttendance, str | None]:
    """ensure 鎖列後以條件式 UPDATE 改為 present；回傳 (最新列, 失敗的錯誤碼或 None)。"""
    row = ensure_attendance_row(session, student_id, d)
    hit = session.execute(
        update(StudentAttendance)
        .where(StudentAttendance.id == row.id, StudentAttendance.status.in_(("expected", "absent")))
        .values(
            status="present",
            check_in_at=now,
            check_in_source="manual",
            updated_by=actor_id,
            note=func.coalesce(note, StudentAttendance.note),
        )
        .returning(StudentAttendance.id)
    ).scalar_one_or_none()
    row = _reload(session, row.id)
    if hit is not None:
        return row, None
    if row.status == "present":
        return row, "already_checked_in"
    if row.status == "left":
        return row, "already_checked_out"
    return row, "student_on_leave"


def check_in(
    session: Session,
    student_id: UUID,
    *,
    actor: CurrentStaff,
    note: str | None,
    clock: Clock,
) -> AttendanceRowOut:
    """expected / absent → present（缺席後到班即遲到情境）；通知家長並推播。不 commit。

    出勤列先以 ``ensure_attendance_row`` 鎖定，再以條件式 UPDATE 寫入：兩位員工同時按時後到者看到
    present → 409，通知只發一次。移植 ivy ``api/student_attendance.py::batch_save_attendance`` 的
    單筆寫入；去掉教師端班級權限、中文狀態值、tenant。
    """
    d = clock.today()
    student = _require_active_on_service_day(session, student_id, d)
    now = clock.now()
    row, conflict = _try_check_in(session, student_id, d, actor_id=actor.id, note=note, now=now)
    if conflict is not None:
        raise ConflictError(conflict, _CHECK_IN_CONFLICTS[conflict])

    enqueue(
        session,
        Event.ATTENDANCE_CHECKED_IN,
        recipients=parent_recipients(session, student_id),
        payload={
            "student_id": student_id,
            "student_name": student.name,
            "time": to_taipei(now).strftime("%H:%M"),
        },
        clock=clock,
    )
    return _publish_row(session, row, clock=clock)


def mark_absent(
    session: Session,
    student_id: UUID,
    *,
    actor: CurrentStaff,
    note: str | None,
    clock: Clock,
) -> AttendanceRowOut:
    """expected → absent；已是 absent 原樣回傳（冪等）。缺席沒有通知事件（M9），只推播。"""
    d = clock.today()
    _require_active_on_service_day(session, student_id, d)
    row = ensure_attendance_row(session, student_id, d)
    hit = session.execute(
        update(StudentAttendance)
        .where(StudentAttendance.id == row.id, StudentAttendance.status == "expected")
        .values(
            status="absent",
            updated_by=actor.id,
            note=func.coalesce(note, StudentAttendance.note),
        )
        .returning(StudentAttendance.id)
    ).scalar_one_or_none()
    row = _reload(session, row.id)
    if hit is None:
        if row.status == "absent":
            return _single_row_out(session, row)
        if row.status in ("present", "left"):
            raise ConflictError("already_checked_in", "學生今天已登記到班，要更正請使用改判")
        raise ConflictError("student_on_leave", "學生今天請假")
    return _publish_row(session, row, clock=clock)


def get_child_monthly_attendance(
    session: Session, student_id: UUID, month: str, *, clock: Clock
) -> ParentMonthlyAttendanceOut:
    """家長端單一小孩月出勤（呼叫端已驗證所有權）；stats 規則同 get_monthly_attendance。

    不回傳 note、updated_by 等內部欄位。移植 ivy ``api/parent_portal/attendance.py::
    get_monthly_attendance``；去掉 tenant 與 SQLite 分支。
    """
    month_start, month_end = _parse_month(month)
    today = clock.today()
    service_days = set(list_service_days(session, month_start, month_end))
    enrolled_on = session.execute(
        select(Student.enrolled_on).where(Student.id == student_id)
    ).scalar_one_or_none()
    rows = {
        row[0]: row[1:]
        for row in session.execute(
            select(
                StudentAttendance.service_date,
                StudentAttendance.status,
                StudentAttendance.check_in_at,
                StudentAttendance.check_out_at,
                StudentLeave.leave_type,
            )
            .outerjoin(StudentLeave, StudentLeave.id == StudentAttendance.leave_id)
            .where(
                StudentAttendance.student_id == student_id,
                StudentAttendance.service_date.between(month_start, month_end),
            )
        )
    }

    days: list[ParentAttendanceDayOut] = []
    statuses: list[AttendanceStatus | None] = []
    counted: list[bool] = []
    d = month_start
    while d <= month_end:
        is_service = d in service_days
        status, check_in_at, check_out_at, leave_type = rows.get(d, (None, None, None, None))
        days.append(
            ParentAttendanceDayOut(
                date=d,
                is_service_day=is_service,
                status=status,
                check_in_at=check_in_at,
                check_out_at=check_out_at,
                leave_type=leave_type if status == "leave" else None,
            )
        )
        statuses.append(status if is_service else None)
        counted.append(is_service and d <= today and (enrolled_on is None or d >= enrolled_on))
        d += timedelta(days=1)
    return ParentMonthlyAttendanceOut(
        student_id=student_id,
        month=f"{month_start.year:04d}-{month_start.month:02d}",
        days=days,
        stats=_monthly_stats(statuses, counted),
    )


def check_out(
    session: Session,
    student_id: UUID,
    *,
    actor: CurrentStaff,
    note: str | None,
    clock: Clock,
) -> AttendanceRowOut:
    """present → left；通知家長並推播。當日沒有出勤列 → 409（不建立列）。不 commit。

    條件式 UPDATE（status='present' 且 check_in_at <= now）：兩次同時離班只有一次命中，通知一則。
    """
    student = get_student_or_404(session, student_id)
    d = clock.today()
    row = session.execute(
        select(StudentAttendance)
        .where(StudentAttendance.student_id == student_id, StudentAttendance.service_date == d)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if row is None:
        raise ConflictError("not_checked_in", "學生今天尚未登記到班")
    now = clock.now()
    hit = session.execute(
        update(StudentAttendance)
        .where(
            StudentAttendance.id == row.id,
            StudentAttendance.status == "present",
            StudentAttendance.check_in_at <= now,
        )
        .values(
            status="left",
            check_out_at=now,
            check_out_source="manual",
            updated_by=actor.id,
            note=func.coalesce(note, StudentAttendance.note),
        )
        .returning(StudentAttendance.id)
    ).scalar_one_or_none()
    row = _reload(session, row.id)
    if hit is None:
        if row.status in ("expected", "absent"):
            raise ConflictError("not_checked_in", "學生今天尚未登記到班")
        if row.status == "left":
            raise ConflictError("already_checked_out", "學生今天已離班")
        if row.status == "leave":
            raise ConflictError("student_on_leave", "學生今天請假")
        raise ConflictError("invalid_check_out_time", "離班時間不可早於到班時間")

    enqueue(
        session,
        Event.ATTENDANCE_CHECKED_OUT,
        recipients=parent_recipients(session, student_id),
        payload={
            "student_id": student_id,
            "student_name": student.name,
            "time": to_taipei(now).strftime("%H:%M"),
        },
        clock=clock,
    )
    return _publish_row(session, row, clock=clock)


def mark_left_by_pickup(
    session: Session, student_id: UUID, service_date: date, *, at: datetime, clock: Clock
) -> bool:
    """接送完成時把出勤改為 left（check_out_source='pickup'）；不是 present 時不改並回 False。

    接送完成本身不因出勤狀態失敗；不發 attendance.checked_out（接送完成已發 pickup.completed），
    updated_by 不填（接送模組已記錄 completed_by）。
    """
    row_id = session.execute(
        update(StudentAttendance)
        .where(
            StudentAttendance.student_id == student_id,
            StudentAttendance.service_date == service_date,
            StudentAttendance.status == "present",
            StudentAttendance.check_in_at <= at,
        )
        .values(status="left", check_out_at=at, check_out_source="pickup")
        .returning(StudentAttendance.id)
    ).scalar_one_or_none()
    if row_id is None:
        logger.info(
            "接送完成但出勤不是已到班，不改出勤 student_id=%s service_date=%s",
            student_id,
            service_date,
        )
        return False
    _publish_row(session, _reload(session, row_id), clock=clock)
    return True


_SKIP_MESSAGES: Final = {
    "student_not_found": "找不到學生",
    "student_not_active": "學生目前不在學",
    **_CHECK_IN_CONFLICTS,
}


def batch_check_in(
    session: Session, student_ids: Sequence[UUID], *, actor: CurrentStaff, clock: Clock
) -> BatchCheckInOut:
    """依輸入順序逐生到班（與 check_in 相同的 ensure + 條件式更新），失敗收進 skipped 不中斷。

    家長收件人一次查詢、每位成功學生一則通知；admin 只廣播一次 attendance.batch_updated，家長端
    逐生收到 attendance.updated。移植 ivy ``batch_save_attendance`` 的「整批送出、逐筆寫入」。
    """
    d = clock.today()
    if not is_service_day(session, d):
        raise ConflictError("not_service_day", "今天不是營業日")
    students = {
        row.id: row
        for row in session.execute(
            select(Student.id, Student.name, Student.status, Student.archived_at).where(
                Student.id.in_(student_ids)
            )
        )
    }
    now = clock.now()
    outcome: dict[UUID, StudentAttendance | str] = {}
    eligible: list[UUID] = []
    for student_id in student_ids:
        student = students.get(student_id)
        if student is None or student.archived_at is not None:
            outcome[student_id] = "student_not_found"
        elif student.status != "active":
            outcome[student_id] = "student_not_active"
        else:
            eligible.append(student_id)
    # 上鎖依 student_id 排序（與 lock_progress_row / BACKEND-378 相同慣例）：兩個反序批次同時跑
    # 時不會各握一列互等成死結；回報仍依輸入順序
    for student_id in sorted(eligible):
        row, code = _try_check_in(session, student_id, d, actor_id=actor.id, note=None, now=now)
        outcome[student_id] = code if code is not None else row

    rows: list[StudentAttendance] = []
    skipped: list[BatchSkipOut] = []
    for student_id in student_ids:
        result = outcome[student_id]
        if isinstance(result, str):
            skipped.append(
                BatchSkipOut(student_id=student_id, code=result, message=_SKIP_MESSAGES[result])
            )
        else:
            rows.append(result)

    if not rows:
        return BatchCheckInOut(succeeded=[], skipped=skipped)
    briefs = student_brief_map(session, [row.student_id for row in rows])
    succeeded = [_row_out(row, briefs[row.student_id], None) for row in rows]
    recipients = parent_recipients_bulk(session, [row.student_id for row in rows])
    time_text = to_taipei(now).strftime("%H:%M")
    for row in rows:
        enqueue(
            session,
            Event.ATTENDANCE_CHECKED_IN,
            recipients=recipients[row.student_id],
            payload={
                "student_id": row.student_id,
                "student_name": students[row.student_id].name,
                "time": time_text,
            },
            clock=clock,
        )
        push_to_student_after_commit(
            session,
            row.student_id,
            topic="attendance",
            type="attendance.updated",
            data=to_parent_attendance_event(row),
            clock=clock,
        )
    broadcast_after_commit(
        session,
        topic="attendance",
        type="attendance.batch_updated",
        data={"items": [out.model_dump() for out in succeeded]},
        clock=clock,
    )
    return BatchCheckInOut(succeeded=succeeded, skipped=skipped)
