"""作業進度 service（domain_spec M6）。

- BACKEND-373：``lock_progress_row``（同一學生同一天的進度寫入在此列上序列化）。
- BACKEND-384：``get_child_homework``（家長端當日作業明細，呼叫端已驗證所有權）。
- BACKEND-374：``broadcast_homework_snapshot``（寫入方法共用的 ws 推播；同交易同一學生同一天只推最後
  狀態）。
- BACKEND-383：``get_board``（作業進度看板，逐生卡片）。
- BACKEND-376：``handle_homework_done``（整體轉為 done 的副作用：通知家長、同步接送回覆）。
- BACKEND-382：``set_ready_eta_and_note``（設定預計可接送時間與說明，通知 homework.eta_updated）。
- BACKEND-375：``recompute_progress``（項目異動後重算整體進度；轉 done 觸發 handle_homework_done）。
- BACKEND-381：``set_overall_status``（員工手動標整體完成 / 改回由項目推導）。
- BACKEND-377：``create_item``（新增單一學生作業項目並重算進度）。
- BACKEND-378：``batch_create_items``（整班批次新增同一份作業，依 student_id 排序鎖進度列）。
- BACKEND-379：``update_item``（修改作業項目；先鎖項目列再重算進度）。

鎖序一律「進度列 → 請求列」：寫入方法先 ``lock_progress_row``，之後才可能由 sync_open_request_reply
鎖接送請求（與 BACKEND-407 / 413 一致）。項目寫入（新增 / 修改 / 刪除）是先寫項目列、再由
``recompute_progress`` 鎖進度列；沒有任何流程在鎖了進度列之後才寫項目列，所以兩者不會互等成環。
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any, Final, Literal
from uuid import UUID

from sqlalchemy import event, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, SessionTransaction

from app.core.clock import Clock
from app.core.errors import AppError, ConflictError, NotFoundError
from app.core.settings_registry import HOMEWORK_WINDOW
from app.models.account import StaffUser
from app.models.attendance import AttendanceStatus, StudentAttendance
from app.models.classes import SchoolClass
from app.models.homework import HomeworkDailyProgress, HomeworkItem
from app.models.reference import Subject
from app.models.students import Student
from app.notifications.events import Event
from app.notifications.recipients import parent_recipients
from app.notifications.service import enqueue
from app.realtime.publish import broadcast_after_commit
from app.repositories.students import (
    get_class_or_404,
    get_student_or_404,
    list_active_student_ids,
)
from app.schemas.homework import (
    BoardOut,
    BoardQuery,
    BoardStudentOut,
    BoardSummaryOut,
    BoardWindowOut,
    HomeworkBatchCreateIn,
    HomeworkBatchOut,
    HomeworkItemCreateIn,
    HomeworkItemOut,
    HomeworkItemUpdateIn,
    HomeworkMutationOut,
    ParentHomeworkItemOut,
    ParentHomeworkOut,
    ProgressOut,
)
from app.services.homework_rules import derive_overall_status
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


def _progress_view(session: Session, progress: HomeworkDailyProgress) -> ProgressOut:
    """進度列 → ``ProgressOut``（含預計可接送時間最後修改者的姓名）。"""
    eta_by = None
    if progress.eta_updated_by is not None:
        eta_by = session.execute(
            select(StaffUser.display_name).where(StaffUser.id == progress.eta_updated_by)
        ).scalar_one_or_none()
    return _progress_out(progress.student_id, progress.service_date, progress, eta_by)


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
    return _progress_view(session, progress)


@dataclass(frozen=True)
class ProgressChange:
    progress: HomeworkDailyProgress
    old_status: str
    new_status: str


def _apply_overall(
    session: Session,
    progress: HomeworkDailyProgress,
    new_status: str,
    *,
    clock: Clock,
) -> ProgressChange:
    """寫回整體狀態；今天的「非 done → done」才觸發 handle_homework_done；最後推作業快照。"""
    old_status = progress.overall_status
    progress.overall_status = new_status  # type: ignore[assignment]
    session.flush()
    if old_status != "done" and new_status == "done" and progress.service_date == clock.today():
        handle_homework_done(session, progress.student_id, progress.service_date, clock=clock)
    broadcast_homework_snapshot(session, progress.student_id, progress.service_date, clock=clock)
    return ProgressChange(progress=progress, old_status=old_status, new_status=new_status)


def _derived_status(session: Session, student_id: UUID, service_date: date) -> str:
    # 在進度列鎖之後才查：READ COMMITTED 下看得到先前已 commit 的其他交易寫入
    statuses = session.execute(
        select(HomeworkItem.status).where(
            HomeworkItem.student_id == student_id, HomeworkItem.service_date == service_date
        )
    ).scalars()
    return derive_overall_status(list(statuses))


def recompute_progress(
    session: Session, student_id: UUID, service_date: date, *, clock: Clock
) -> ProgressChange:
    """項目異動後依全部項目重算整體進度（覆蓋先前的手動 done）。

    兩位員工同時完成同一學生最後兩個項目時，兩個交易在進度列上排隊，後者看到前者已 commit 的項目，
    只有真正觀察到「非 done → done」的那一個交易發 homework.done。
    """
    progress = lock_progress_row(session, student_id, service_date)
    return _apply_overall(
        session, progress, _derived_status(session, student_id, service_date), clock=clock
    )


def set_overall_status(
    session: Session,
    student_id: UUID,
    service_date: date,
    mode: Literal["done", "auto"],
    *,
    actor: CurrentStaff,
    clock: Clock,
) -> ProgressChange:
    """mode='done' 不論項目直接標整體完成（例如當天無作業）；'auto' 改回由項目推導。"""
    get_student_or_404(session, student_id)
    check_service_date(session, service_date, clock=clock)
    progress = lock_progress_row(session, student_id, service_date)
    new_status = "done" if mode == "done" else _derived_status(session, student_id, service_date)
    return _apply_overall(session, progress, new_status, clock=clock)


def _require_active_subject(session: Session, subject_id: UUID | None) -> Subject | None:
    """subject_id 給值時必須是存在且啟用中的科目，否則 422 ``invalid_subject``。"""
    if subject_id is None:
        return None
    subject = session.execute(
        select(Subject).where(Subject.id == subject_id, Subject.is_active.is_(True))
    ).scalar_one_or_none()
    if subject is None:
        raise AppError("invalid_subject", "科目不存在或已停用", status=422)
    return subject


def create_item(
    session: Session, data: HomeworkItemCreateIn, *, actor: CurrentStaff, clock: Clock
) -> HomeworkMutationOut:
    """新增單一學生的作業項目，重算整體進度並回傳項目與最新進度。

    學生須在學（不存在 / 已封存 404、非 active 409）、科目須啟用中、日期須在 homework.window 內
    （沒給日期用台北今天）。項目寫入後由 ``recompute_progress`` 鎖進度列重算：新增者 commit 前一直
    持有該鎖，同時把最後一個未完成項目改成 done 的人必須排隊，才不會漏算這個新項目。
    """
    student = get_student_or_404(session, data.student_id)
    if student.status != "active":
        raise ConflictError("student_not_active", "學生目前不在學，無法新增作業項目")
    subject = _require_active_subject(session, data.subject_id)
    service_date = data.service_date or clock.today()
    check_service_date(session, service_date, clock=clock)

    item = HomeworkItem(
        student_id=student.id,
        service_date=service_date,
        subject=subject,
        title=data.title,
        status=data.status,
        sort_order=data.sort_order,
        updated_by=actor.id,
    )
    session.add(item)
    session.flush()
    change = recompute_progress(session, student.id, service_date, clock=clock)
    return HomeworkMutationOut(
        item=_item_out(item), progress=_progress_view(session, change.progress)
    )


def batch_create_items(
    session: Session, data: HomeworkBatchCreateIn, *, actor: CurrentStaff, clock: Clock
) -> HomeworkBatchOut:
    """整班（或班內指定的學生）各新增一個相同的作業項目，逐生重算整體進度。

    對象是班級內在學（active、未封存）的學生；給了 student_ids 時，任何一個不是該班在學學生
    （含他班、暫停、退班、不存在）都整批拒絕，不會默默略過。每位學生的 sort_order 接在該生當天
    現有項目之後（一次查詢取得各生最大值）。項目全部寫入後，依 student_id 排序逐一
    ``recompute_progress``：批次鎖進度列一律依主鍵排序，與其他批次交易不會互等成環。回傳的
    items 依班級內的學號順序。
    """
    get_class_or_404(session, data.class_id)
    subject = _require_active_subject(session, data.subject_id)
    service_date = data.service_date or clock.today()
    check_service_date(session, service_date, clock=clock)

    in_class = list_active_student_ids(session, class_id=data.class_id)
    if data.student_ids is None:
        targets = in_class
    else:
        active = set(in_class)
        outside = [student_id for student_id in data.student_ids if student_id not in active]
        if outside:
            raise AppError(
                "student_not_in_class",
                "指定的學生不是這個班級的在學學生",
                status=422,
                details={"student_ids": outside},
            )
        wanted = set(data.student_ids)
        targets = [student_id for student_id in in_class if student_id in wanted]
    if not targets:
        raise AppError("no_students", "這個班級沒有可新增作業的在學學生", status=422)

    top_sort_order = {
        student_id: top
        for student_id, top in session.execute(
            select(HomeworkItem.student_id, func.max(HomeworkItem.sort_order))
            .where(HomeworkItem.student_id.in_(targets), HomeworkItem.service_date == service_date)
            .group_by(HomeworkItem.student_id)
        )
    }
    items = [
        HomeworkItem(
            student_id=student_id,
            service_date=service_date,
            subject=subject,
            title=data.title,
            status="todo",
            sort_order=top_sort_order[student_id] + 1 if student_id in top_sort_order else 0,
            updated_by=actor.id,
        )
        for student_id in targets
    ]
    session.add_all(items)
    session.flush()
    for student_id in sorted(targets):
        recompute_progress(session, student_id, service_date, clock=clock)
    return HomeworkBatchOut(created=len(items), items=[_item_out(item) for item in items])


def _lock_item_or_404(session: Session, item_id: UUID) -> HomeworkItem:
    """FOR UPDATE 鎖住項目列並回傳 DB 現值；不存在（含剛被別的交易刪除）→ 404。

    ``subject`` 是 joined（outer join），只鎖項目列（``of=HomeworkItem``）。同一項目的修改與刪除在
    此序列化：排隊的人等前一個 commit 後重新判定，被刪掉就是 404，不會在 UPDATE 時撞上 0 列。
    """
    item = session.execute(
        select(HomeworkItem)
        .where(HomeworkItem.id == item_id)
        .with_for_update(of=HomeworkItem)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if item is None:
        raise NotFoundError("homework_item_not_found", "找不到作業項目")
    return item


def update_item(
    session: Session,
    item_id: UUID,
    data: HomeworkItemUpdateIn,
    *,
    actor: CurrentStaff,
    clock: Clock,
) -> HomeworkMutationOut:
    """只更新請求有給的欄位並記錄 updated_by，之後一律重算整體進度（status 沒變也重算，讓看板
    收到最新快照；``homework.done`` 只在整體轉為 done 時發出）。

    先鎖項目列、驗證科目（失敗時整筆都不改），再由 ``recompute_progress`` 鎖進度列：鎖序為
    項目列 → 進度列 → 請求列。
    """
    item = _lock_item_or_404(session, item_id)
    fields = data.model_fields_set
    new_subject = (
        _require_active_subject(session, data.subject_id) if "subject_id" in fields else None
    )

    if "subject_id" in fields:
        item.subject = new_subject  # subject_id 給 null 代表清除
    # UpdateModel 保證請求給了的非 nullable 欄位不為 null
    if data.title is not None:
        item.title = data.title
    if data.status is not None:
        item.status = data.status
    if data.sort_order is not None:
        item.sort_order = data.sort_order
    item.updated_by = actor.id
    session.flush()

    change = recompute_progress(session, item.student_id, item.service_date, clock=clock)
    return HomeworkMutationOut(
        item=_item_out(item), progress=_progress_view(session, change.progress)
    )
