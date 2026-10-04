"""作業進度 service（domain_spec M6）。

- BACKEND-373：``lock_progress_row``（同一學生同一天的進度寫入在此列上序列化）。
- BACKEND-384：``get_child_homework``（家長端當日作業明細，呼叫端已驗證所有權）。
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.homework import HomeworkDailyProgress, HomeworkItem
from app.schemas.homework import ParentHomeworkItemOut, ParentHomeworkOut

UQ_PROGRESS_STUDENT_DATE = "uq_homework_daily_progress_student_date"


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
    items = (
        session.execute(
            select(HomeworkItem)
            .where(HomeworkItem.student_id == student_id, HomeworkItem.service_date == service_date)
            .order_by(HomeworkItem.sort_order, HomeworkItem.created_at, HomeworkItem.id)
        )
        .scalars()
        .unique()
        .all()
    )
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
