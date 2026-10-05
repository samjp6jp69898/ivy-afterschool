"""作業進度 service（domain_spec M6）。

- BACKEND-373：``lock_progress_row``（同一學生同一天的進度寫入在此列上序列化）。
- BACKEND-384：``get_child_homework``（家長端當日作業明細，呼叫端已驗證所有權）。
- BACKEND-374：``broadcast_homework_snapshot``（寫入方法共用的 ws 推播；同交易同一學生同一天只推最後
  狀態）。
- BACKEND-383：``get_board``（作業進度看板，逐生卡片）。
- BACKEND-376：``handle_homework_done``（整體轉為 done 的副作用：通知家長、同步接送回覆）。
- BACKEND-382：``set_ready_eta_and_note``（設定預計可接送時間與說明，通知 homework.eta_updated）。
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any, Final
from uuid import UUID

from sqlalchemy import event, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, SessionTransaction

from app.core.clock import Clock
from app.core.errors import AppError
from app.core.settings_registry import HOMEWORK_WINDOW
from app.models.account import StaffUser
from app.models.attendance import AttendanceStatus, StudentAttendance
from app.models.classes import SchoolClass
from app.models.homework import HomeworkDailyProgress, HomeworkItem
from app.models.students import Student
from app.notifications.events import Event
from app.notifications.recipients import parent_recipients
from app.notifications.service import enqueue
from app.realtime.publish import broadcast_after_commit
from app.repositories.students import get_student_or_404
from app.schemas.homework import (
    BoardOut,
    BoardQuery,
    BoardStudentOut,
    BoardSummaryOut,
    BoardWindowOut,
    HomeworkItemOut,
    ParentHomeworkItemOut,
    ParentHomeworkOut,
    ProgressOut,
)
from app.services.pickup.requests import sync_open_request_reply
from app.services.settings_service import get_setting

if TYPE_CHECKING:
    from app.api.deps import CurrentStaff

UQ_PROGRESS_STUDENT_DATE = "uq_homework_daily_progress_student_date"
BROADCAST_KEYS: Final = "homework_broadcast_keys"
_BROADCAST_HOOKED: Final = "homework_broadcast_hooked"
# set_ready_eta_and_note 的「未提供」標記（與給 None 代表清除區分）
UNSET: Final[Any] = object()


def lock_progress_row(
    session: Session, student_id: UUID, service_date: date
) -> HomeworkDailyProgress:
    """取得並鎖定學生某日的進度列，無列時建立 not_started（冪等）。

    讓「讀 items → 計算 overall → 寫回」與「非 done 轉 done」在此列上序列化。批次呼叫者必須依
    student_id 排序後依序鎖定，避免死結（BACKEND-378）。
    """
    session.execute(
        pg_insert(HomeworkDailyProgress)
        .values(student_id=student_id, service_date=service_date)
        .on_conflict_do_nothing(constraint=UQ_PROGRESS_STUDENT_DATE)
    )
    return session.execute(
        select(HomeworkDailyProgress)
        .where(
            HomeworkDailyProgress.student_id == student_id,
            HomeworkDailyProgress.service_date == service_date,
        )
        .with_for_update()
        # 已載入的物件以上鎖後的 DB 現值刷新（別的交易可能剛改過）
        .execution_options(populate_existing=True)
    ).scalar_one()


def get_child_homework(session: Session, student_id: UUID, service_date: date) -> ParentHomeworkOut:
    items = _items_of(session, student_id, service_date)
    progress = session.execute(
        select(HomeworkDailyProgress).where(
            HomeworkDailyProgress.student_id == student_id,
            HomeworkDailyProgress.service_date == service_date,
        )
    ).scalar_one_or_none()

    updated = [item.updated_at for item in items]
    if progress is not None:
        updated.append(progress.updated_at)
    return ParentHomeworkOut(
        student_id=student_id,
        date=service_date,
        items=[
            ParentHomeworkItemOut(
                title=item.title,
                subject_name=item.subject.name if item.subject is not None else None,
                status=item.status,
            )
            for item in items
        ],
        overall_status=progress.overall_status if progress is not None else "not_started",
        ready_eta=(
            progress.ready_eta.strftime("%H:%M")
            if progress is not None and progress.ready_eta is not None
            else None
        ),
        note=progress.note if progress is not None else None,
        updated_at=max(updated) if updated else None,
    )


def _items_of(session: Session, student_id: UUID, service_date: date) -> list[HomeworkItem]:
    return list(
        session.execute(
            select(HomeworkItem)
            .where(HomeworkItem.student_id == student_id, HomeworkItem.service_date == service_date)
            .order_by(HomeworkItem.sort_order, HomeworkItem.created_at, HomeworkItem.id)
        )
        .scalars()
        .unique()
    )


def _item_out(item: HomeworkItem) -> HomeworkItemOut:
    return HomeworkItemOut(
        id=item.id,
        student_id=item.student_id,
        service_date=item.service_date,
        subject_id=item.subject_id,
        subject_name=item.subject.name if item.subject is not None else None,
        title=item.title,
        status=item.status,
        sort_order=item.sort_order,
        updated_at=item.updated_at,
    )


def _progress_out(
    student_id: UUID,
    service_date: date,
    progress: HomeworkDailyProgress | None,
    eta_updated_by_name: str | None,
) -> ProgressOut:
    """沒有進度列 → not_started、無 ETA。"""
    if progress is None:
        return ProgressOut(
            student_id=student_id,
            service_date=service_date,
            overall_status="not_started",
            ready_eta=None,
            note=None,
            eta_updated_at=None,
            eta_updated_by_name=None,
        )
    return ProgressOut(
        student_id=student_id,
        service_date=service_date,
        overall_status=progress.overall_status,
        ready_eta=progress.ready_eta.strftime("%H:%M") if progress.ready_eta is not None else None,
        note=progress.note,
        eta_updated_at=progress.eta_updated_at,
        eta_updated_by_name=eta_updated_by_name,
    )


def _publish_snapshot(session: Session, student_id: UUID, service_date: date, clock: Clock) -> None:
    items = _items_of(session, student_id, service_date)
    progress = session.execute(
        select(HomeworkDailyProgress).where(
            HomeworkDailyProgress.student_id == student_id,
            HomeworkDailyProgress.service_date == service_date,
        )
    ).scalar_one_or_none()
    eta_by = None
    if progress is not None and progress.eta_updated_by is not None:
        eta_by = session.execute(
            select(StaffUser.display_name).where(StaffUser.id == progress.eta_updated_by)
        ).scalar_one_or_none()
    progress_out = _progress_out(student_id, service_date, progress, eta_by)
    broadcast_after_commit(
        session,
        topic="homework",
        type="homework.progress_updated",
        data={
            "student_id": student_id,
            "service_date": service_date,
            "items": [_item_out(item).model_dump() for item in items],
            "progress": progress_out.model_dump(),
        },
        student_id=student_id,
        # 家長版不含員工姓名與 item id
        parent_data={
            "student_id": student_id,
            "date": service_date,
            "items": [
                ParentHomeworkItemOut(
                    title=item.title,
                    subject_name=item.subject.name if item.subject is not None else None,
                    status=item.status,
                ).model_dump()
                for item in items
            ],
            "overall_status": progress_out.overall_status,
            "ready_eta": progress_out.ready_eta,
            "note": progress_out.note,
        },
        clock=clock,
    )


def _flush_broadcasts(session: Session) -> None:
    # begin_nested 的 savepoint 釋放也會發 before_commit：等最外層 commit 才組資料
    if session.in_nested_transaction():
        return
    pending: dict[tuple[UUID, date], Clock] = session.info.pop(BROADCAST_KEYS, {})
    for (student_id, service_date), clock in pending.items():
        _publish_snapshot(session, student_id, service_date, clock)


def _clear_broadcasts(session: Session, previous_transaction: SessionTransaction) -> None:
    if previous_transaction.nested:
        return
    session.info.pop(BROADCAST_KEYS, None)


def broadcast_homework_snapshot(
    session: Session, student_id: UUID, service_date: date, *, clock: Clock
) -> None:
    """登記該生該日的快照推播；commit 前（before_commit）才讀取最終狀態並交給 BACKEND-224 送出。

    同一交易內同一 (student_id, service_date) 只推一次；rollback 時清除登記。
    """
    if not session.info.get(_BROADCAST_HOOKED):
        event.listen(session, "before_commit", _flush_broadcasts)
        event.listen(session, "after_soft_rollback", _clear_broadcasts)
        session.info[_BROADCAST_HOOKED] = True
    # 與 run_after_commit 相同：先開交易，之後的 rollback 才一定會觸發 after_soft_rollback 清掉登記
    if not session.in_transaction():
        session.begin()
    keys: dict[tuple[UUID, date], Clock] = session.info.setdefault(BROADCAST_KEYS, {})
    keys[(student_id, service_date)] = clock


def get_board(session: Session, query: BoardQuery, *, clock: Clock) -> BoardOut:
    """active 未封存學生（class_id 篩選）的當日卡片，依班級 sort_order、班名、學號排序。

    summary 只計當日出勤不是 leave / absent 的學生。SQL 固定：學生、出勤、items、progress（join
    員工姓名）、作業日期範圍設定（快取未命中時）。window 讓課輔老師不需 settings:read 也能取得。
    """
    d = query.date or clock.today()
    stmt = (
        select(Student.id, Student.student_no, Student.name, Student.class_id, SchoolClass.name)
        .outerjoin(SchoolClass, SchoolClass.id == Student.class_id)
        .where(Student.status == "active", Student.archived_at.is_(None))
        .order_by(
            SchoolClass.sort_order.asc().nulls_last(),
            SchoolClass.name.asc().nulls_last(),
            Student.student_no,
            Student.id,
        )
    )
    if query.class_id is not None:
        stmt = stmt.where(Student.class_id == query.class_id)
    students = session.execute(stmt).all()
    ids = [row[0] for row in students]

    attendance: dict[UUID, AttendanceStatus] = {}
    items_by: defaultdict[UUID, list[HomeworkItem]] = defaultdict(list)
    progress_by: dict[UUID, HomeworkDailyProgress] = {}
    eta_by_name: dict[UUID, str | None] = {}
    if ids:
        # Result 有 keys()，dict(result) 會被當成 mapping：改用 comprehension
        attendance = {
            student_id: status
            for student_id, status in session.execute(
                select(StudentAttendance.student_id, StudentAttendance.status).where(
                    StudentAttendance.student_id.in_(ids), StudentAttendance.service_date == d
                )
            )
        }
        for item in session.execute(
            select(HomeworkItem)
            .where(HomeworkItem.student_id.in_(ids), HomeworkItem.service_date == d)
            .order_by(HomeworkItem.sort_order, HomeworkItem.created_at, HomeworkItem.id)
        ).scalars():
            items_by[item.student_id].append(item)
        for progress, eta_by in session.execute(
            select(HomeworkDailyProgress, StaffUser.display_name)
            .outerjoin(StaffUser, StaffUser.id == HomeworkDailyProgress.eta_updated_by)
            .where(
                HomeworkDailyProgress.student_id.in_(ids), HomeworkDailyProgress.service_date == d
            )
        ):
            progress_by[progress.student_id] = progress
            eta_by_name[progress.student_id] = eta_by

    cards: list[BoardStudentOut] = []
    for student_id, student_no, name, class_id, class_name in students:
        cards.append(
            BoardStudentOut(
                student_id=student_id,
                student_no=student_no,
                name=name,
                class_id=class_id,
                class_name=class_name,
                attendance_status=attendance.get(student_id),
                items=[_item_out(item) for item in items_by[student_id]],
                progress=_progress_out(
                    student_id, d, progress_by.get(student_id), eta_by_name.get(student_id)
                ),
            )
        )
    counted = Counter(
        card.progress.overall_status
        for card in cards
        if card.attendance_status not in ("leave", "absent")
    )
    window = get_setting(session, HOMEWORK_WINDOW)
    return BoardOut(
        date=d,
        window=BoardWindowOut(past_days=window.past_days, future_days=window.future_days),
        summary=BoardSummaryOut(
            total=sum(counted.values()),
            done=counted["done"],
            in_progress=counted["in_progress"],
            not_started=counted["not_started"],
        ),
        students=cards,
    )


def check_service_date(session: Session, service_date: date, *, clock: Clock) -> None:
    """作業日期必須在 homework.window（今天 - past_days ~ 今天 + future_days）內，否則 422。"""
    window = get_setting(session, HOMEWORK_WINDOW)
    today = clock.today()
    earliest = today - timedelta(days=window.past_days)
    latest = today + timedelta(days=window.future_days)
    if not earliest <= service_date <= latest:
        raise AppError(
            "invalid_service_date",
            "作業日期超出可編輯的範圍",
            status=422,
            details={"min_date": earliest.isoformat(), "max_date": latest.isoformat()},
        )


def handle_homework_done(
    session: Session, student_id: UUID, service_date: date, *, clock: Clock
) -> None:
    """整體轉為 done 的副作用：通知家長 homework.done、同步進行中的接送回覆。

    只由呼叫端在「今天、非 done → done」時呼叫；本方法不再判斷。
    """
    student_name = session.execute(
        select(Student.name).where(Student.id == student_id)
    ).scalar_one()
    enqueue(
        session,
        Event.HOMEWORK_DONE,
        recipients=parent_recipients(session, student_id),
        payload={"student_id": student_id, "student_name": student_name},
        clock=clock,
    )
    sync_open_request_reply(session, student_id, service_date, clock=clock)


def set_ready_eta_and_note(
    session: Session,
    student_id: UUID,
    service_date: date,
    *,
    ready_eta: Any = UNSET,  # time | None | UNSET
    note: Any = UNSET,  # str | None | UNSET
    actor: CurrentStaff,
    clock: Clock,
) -> ProgressOut:
    """設定 / 清除預計可接送時間與說明（UNSET 不改，None 清除）。

    ETA 設為新的非 null 值、作業未完成且為今天 → 通知家長 homework.eta_updated；只改 note 或清除 ETA
    不通知。ETA 有變動時同步進行中的接送回覆（BACKEND-413），最後推作業快照。
    """
    if ready_eta is UNSET and note is UNSET:
        raise ValueError("ready_eta 與 note 至少要給一個")
    student = get_student_or_404(session, student_id)
    check_service_date(session, service_date, clock=clock)
    progress = lock_progress_row(session, student_id, service_date)

    eta_changed = ready_eta is not UNSET and ready_eta != progress.ready_eta
    if eta_changed:
        progress.ready_eta = ready_eta
        # 清除時也記錄最後一次修改的人與時間
        progress.eta_updated_by = actor.id
        progress.eta_updated_at = clock.now()
    if note is not UNSET:
        progress.note = note
    session.flush()

    if (
        eta_changed
        and ready_eta is not None
        and progress.overall_status != "done"
        and service_date == clock.today()
    ):
        payload: dict[str, Any] = {
            "student_id": student_id,
            "student_name": student.name,
            "ready_eta": ready_eta.strftime("%H:%M"),
        }
        if progress.note:
            payload["note"] = progress.note
        enqueue(
            session,
            Event.HOMEWORK_ETA_UPDATED,
            recipients=parent_recipients(session, student_id),
            payload=payload,
            clock=clock,
        )
    if eta_changed:
        sync_open_request_reply(session, student_id, service_date, clock=clock)
    broadcast_homework_snapshot(session, student_id, service_date, clock=clock)

    eta_by = None
    if progress.eta_updated_by is not None:
        eta_by = session.execute(
            select(StaffUser.display_name).where(StaffUser.id == progress.eta_updated_by)
        ).scalar_one_or_none()
    return _progress_out(student_id, service_date, progress, eta_by)
