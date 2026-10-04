"""成績模組 service（domain_spec M8）。

- BACKEND-452：``resolve_exam_roster``。
- BACKEND-464：``get_student_exam_history``（後台個人歷次成績，含 draft）。
- BACKEND-465 / 466：家長端成績列表 / 明細，呼叫端已驗證所有權；只看 ``published`` 且該生有成績列
  （含缺考）的考試，草稿、無成績列與不存在的考試一律同一個 404 ``exam_not_found``。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from typing import cast
from uuid import UUID

from sqlalchemy import ColumnElement, select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.core.pagination import Page, PageParams, paginate
from app.models.exams import Exam, ExamScore
from app.repositories.students import get_student_or_404, list_active_student_ids
from app.schemas.exams import (
    HistoryScoreOut,
    ParentExamDetailOut,
    ParentExamListItemOut,
    ParentExamSubjectScoreOut,
    StudentExamHistoryItemOut,
)


def resolve_exam_roster(session: Session, exam: Exam) -> list[UUID]:
    """依 exam 的 class_id / grade_level 決定「目前」的應考學生（active、未封存），依 student_no。

    兩者皆有 → 交集；已退班但已有成績的學生由呼叫端以成績列補回（BACKEND-459）。
    """
    grade_levels = None if exam.grade_level is None else [exam.grade_level]
    return list_active_student_ids(session, class_id=exam.class_id, grade_levels=grade_levels)


def _has_scores_of(student_id: UUID) -> ColumnElement[bool]:
    return Exam.id.in_(select(ExamScore.exam_id).where(ExamScore.student_id == student_id))


def _scores_by_subject(
    session: Session, student_id: UUID, exam_ids: Iterable[UUID]
) -> dict[tuple[UUID, UUID], ExamScore]:
    rows = session.execute(
        select(ExamScore).where(ExamScore.student_id == student_id, ExamScore.exam_id.in_(exam_ids))
    ).scalars()
    return {(row.exam_id, row.subject_id): row for row in rows}


def get_student_exam_history(session: Session, student_id: UUID) -> list[StudentExamHistoryItemOut]:
    """該生有任何成績列的考試（含 draft），exam_date desc；每場列出全部科目（sort_order）。

    固定查詢數：考試（含 type / subjects）+ 該生的成績列。
    """
    get_student_or_404(session, student_id, include_archived=True)
    exams = (
        session.execute(
            select(Exam)
            .where(_has_scores_of(student_id))
            .order_by(Exam.exam_date.desc(), Exam.created_at.desc(), Exam.id)
        )
        .scalars()
        .unique()
        .all()
    )
    scores = _scores_by_subject(session, student_id, [exam.id for exam in exams])
    items: list[StudentExamHistoryItemOut] = []
    for exam in exams:
        history_scores = []
        for subject in exam.subjects:
            row = scores.get((exam.id, subject.subject_id))
            history_scores.append(
                HistoryScoreOut(
                    subject_id=subject.subject_id,
                    subject_name=subject.subject.name,
                    full_score=subject.full_score,
                    score=row.score if row is not None else None,
                    is_absent=row.is_absent if row is not None else False,
                )
            )
        items.append(
            StudentExamHistoryItemOut(
                exam_id=exam.id,
                exam_name=exam.name,
                exam_type_name=exam.exam_type.name,
                exam_date=exam.exam_date,
                status=exam.status,
                scores=history_scores,
            )
        )
    return items


def list_child_exams(
    session: Session, student_id: UUID, page: PageParams
) -> Page[ParentExamListItemOut]:
    stmt = (
        select(Exam)
        .where(Exam.status == "published", _has_scores_of(student_id))
        .order_by(Exam.exam_date.desc(), Exam.id)
    )
    exams, total = paginate(session, stmt, page)
    items = []
    for exam in exams:
        items.append(
            ParentExamListItemOut(
                exam_id=exam.id,
                name=exam.name,
                exam_type_name=exam.exam_type.name,
                exam_date=exam.exam_date,
                published_at=cast(dt.datetime, exam.published_at),  # published 必有（DB 約束）
                subject_count=len(exam.subjects),
            )
        )
    return Page(items=items, total=total)


def get_child_exam_detail(session: Session, student_id: UUID, exam_id: UUID) -> ParentExamDetailOut:
    exam = (
        session.execute(
            select(Exam).where(
                Exam.id == exam_id, Exam.status == "published", _has_scores_of(student_id)
            )
        )
        .scalars()
        .unique()
        .one_or_none()
    )
    if exam is None:
        raise NotFoundError("exam_not_found", "找不到成績")
    scores = _scores_by_subject(session, student_id, [exam.id])
    subjects = []
    for subject in exam.subjects:
        row = scores.get((exam.id, subject.subject_id))
        subjects.append(
            ParentExamSubjectScoreOut(
                subject_name=subject.subject.name,
                full_score=subject.full_score,
                score=row.score if row is not None else None,
                is_absent=row.is_absent if row is not None else False,
                note=row.note if row is not None else None,
            )
        )
    return ParentExamDetailOut(
        exam_id=exam.id,
        name=exam.name,
        exam_type_name=exam.exam_type.name,
        exam_date=exam.exam_date,
        note=exam.note,
        subjects=subjects,
    )
