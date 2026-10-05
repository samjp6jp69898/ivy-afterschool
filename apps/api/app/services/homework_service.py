"""作業進度 service（domain_spec M6）。

- BACKEND-373：``lock_progress_row``（同一學生同一天的進度寫入在此列上序列化）。
- BACKEND-384：``get_child_homework``（家長端當日作業明細，呼叫端已驗證所有權）。
- BACKEND-374：``broadcast_homework_snapshot``（寫入方法共用的 ws 推播；同交易同一學生同一天只推最後
  狀態）。
"""

from __future__ import annotations

from datetime import date
from typing import Final
from uuid import UUID

from sqlalchemy import event, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, SessionTransaction

from app.core.clock import Clock
from app.models.account import StaffUser
from app.models.homework import HomeworkDailyProgress, HomeworkItem
from app.realtime.publish import broadcast_after_commit
from app.schemas.homework import (
    HomeworkItemOut,
    ParentHomeworkItemOut,
    ParentHomeworkOut,
    ProgressOut,
)

UQ_PROGRESS_STUDENT_DATE = "uq_homework_daily_progress_student_date"
BROADCAST_KEYS: Final = "homework_broadcast_keys"
_BROADCAST_HOOKED: Final = "homework_broadcast_hooked"


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
