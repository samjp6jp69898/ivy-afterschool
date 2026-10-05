"""出勤 service（domain_spec M4）。

- BACKEND-302：``ensure_attendance_row``（取得或建立單一學生某日出勤列，check_in / mark_absent /
  batch_check_in 共用）。
- BACKEND-310：``amend_attendance``（改判已登記出勤，寫 audit，commit 後推播 admin / 家長）。
- BACKEND-311：``get_daily_attendance``（每日出勤清單與統計；營業日尚無列的 active 學生以虛擬列
  呈現）。
- BACKEND-312：``get_monthly_attendance``（月出勤報表；入班前與未來日期不計）。
"""

from __future__ import annotations

import calendar
from collections import Counter
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import Select, exists, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.clock import Clock, to_taipei
from app.core.errors import AppError, ConflictError, NotFoundError
from app.models.attendance import AttendanceStatus, StudentAttendance
from app.models.classes import SchoolClass
from app.models.leaves import LEAVE_TYPE_LABELS, StudentLeave
from app.models.students import Student
from app.realtime.publish import broadcast_after_commit
from app.repositories.students import StudentBrief, student_brief_map
from app.schemas.attendance import (
    AttendanceAmendIn,
    AttendanceRowOut,
    DailyAttendanceOut,
    DailyAttendanceQuery,
    DailySummaryOut,
    LeaveBriefOut,
    MonthDayOut,
    MonthlyAttendanceOut,
    MonthlyAttendanceQuery,
    MonthlyStatsOut,
    MonthlyStudentRowOut,
    to_parent_attendance_event,
)
from app.services.audit_service import Actor, record
from app.services.service_calendar import is_service_day, list_service_days

if TYPE_CHECKING:
    from app.api.deps import CurrentStaff
    from app.core.request_meta import RequestMeta


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
                .where(Student.status == "active", Student.archived_at.is_(None), ~has_row),
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
