"""成績模組 service（domain_spec M8）。

- BACKEND-452：``resolve_exam_roster``。
- BACKEND-464：``get_student_exam_history``（後台個人歷次成績，含 draft）。
- BACKEND-465 / 466：家長端成績列表 / 明細，呼叫端已驗證所有權；只看 ``published`` 且該生有成績列
  （含缺考）的考試，草稿、無成績列與不存在的考試一律同一個 404 ``exam_not_found``。
- BACKEND-453：``list_exams``（後台考試列表，篩選 + 分頁）。
- BACKEND-454：``get_exam`` / ``get_exam_or_404``（考試詳情；本模組其他方法共用的取得與鎖定）。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from typing import cast
from uuid import UUID

from sqlalchemy import ColumnElement, Select, and_, func, or_, select
from sqlalchemy.orm import Session, defaultload

from app.core.errors import NotFoundError
from app.core.pagination import Page, PageParams, paginate
from app.models.account import StaffUser
from app.models.classes import SchoolClass
from app.models.exams import Exam, ExamScore
from app.models.students import Student
from app.repositories.students import get_student_or_404, list_active_student_ids
from app.schemas.classes import ClassBriefOut
from app.schemas.exams import (
    ExamListQuery,
    ExamOut,
    ExamSubjectOut,
    ExamTypeBriefOut,
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


def _exam_select() -> Select[Exam]:
    # 班級只需要 id / 名稱：staff_links 改為用到才載入（預設 selectin 會多一條查詢）
    return select(Exam).options(defaultload(Exam.school_class).lazyload(SchoolClass.staff_links))


def _roster_counts(session: Session, exam_ids: list[UUID]) -> dict[UUID, int]:
    """各考試目前的應考人數（與 resolve_exam_roster 同條件），單一 GROUP BY 查詢。"""
    if not exam_ids:
        return {}
    in_roster = and_(
        Student.status == "active",
        Student.archived_at.is_(None),
        or_(Exam.class_id.is_(None), Student.class_id == Exam.class_id),
        or_(Exam.grade_level.is_(None), Student.grade_level == Exam.grade_level),
    )
    rows = session.execute(
        select(Exam.id, func.count(Student.id))
        .join(Student, in_roster)
        .where(Exam.id.in_(exam_ids))
        .group_by(Exam.id)
    )
    return {exam_id: count for exam_id, count in rows}


def _exam_outs(session: Session, exams: list[Exam]) -> list[ExamOut]:
    """應考人數與發布者姓名各以一次查詢批次解析。"""
    roster = _roster_counts(session, [exam.id for exam in exams])
    publisher_ids = {exam.published_by for exam in exams if exam.published_by is not None}
    names: dict[UUID, str] = {}
    if publisher_ids:
        names = {
            staff_id: name
            for staff_id, name in session.execute(
                select(StaffUser.id, StaffUser.display_name).where(StaffUser.id.in_(publisher_ids))
            )
        }
    return [
        ExamOut(
            id=exam.id,
            name=exam.name,
            exam_type=ExamTypeBriefOut(id=exam.exam_type.id, name=exam.exam_type.name),
            exam_date=exam.exam_date,
            grade_level=exam.grade_level,
            class_=(
                ClassBriefOut(id=exam.school_class.id, name=exam.school_class.name)
                if exam.school_class is not None
                else None
            ),
            status=exam.status,
            published_at=exam.published_at,
            published_by_name=names.get(exam.published_by) if exam.published_by else None,
            note=exam.note,
            subjects=[
                ExamSubjectOut(
                    subject_id=subject.subject_id,
                    subject_name=subject.subject.name,
                    full_score=subject.full_score,
                    sort_order=subject.sort_order,
                )
                for subject in exam.subjects
            ],
            roster_count=roster.get(exam.id, 0),
            created_at=exam.created_at,
        )
        for exam in exams
    ]


def list_exams(session: Session, query: ExamListQuery, page: PageParams) -> Page[ExamOut]:
    """篩選後依 exam_date desc、created_at desc 分頁。

    SQL 固定：count、列表（type / 班級 joined）、科目（selectin）、應考人數、發布者姓名。
    """
    stmt = _exam_select()
    if query.q:
        stmt = stmt.where(Exam.name.icontains(query.q, autoescape=True))
    if query.status is not None:
        stmt = stmt.where(Exam.status == query.status)
    if query.exam_type_id is not None:
        stmt = stmt.where(Exam.exam_type_id == query.exam_type_id)
    if query.class_id is not None:
        stmt = stmt.where(Exam.class_id == query.class_id)
    if query.grade_level is not None:
        stmt = stmt.where(Exam.grade_level == query.grade_level)
    if query.date_from is not None:
        stmt = stmt.where(Exam.exam_date >= query.date_from)
    if query.date_to is not None:
        stmt = stmt.where(Exam.exam_date <= query.date_to)
    stmt = stmt.order_by(Exam.exam_date.desc(), Exam.created_at.desc(), Exam.id)

    exams, total = paginate(session, stmt, page)
    return Page(items=_exam_outs(session, exams), total=total)


def get_exam_or_404(
    session: Session, exam_id: UUID, *, for_update: bool = False, for_share: bool = False
) -> Exam:
    """for_update → FOR UPDATE、for_share → FOR SHARE（只鎖 exams：班級是 outer join 的可空側）。"""
    if for_update and for_share:
        raise ValueError("for_update 與 for_share 只能擇一")
    stmt = _exam_select().where(Exam.id == exam_id)
    if for_update or for_share:
        # populate_existing：同 session 已載入過該考試時，以上鎖後的 DB 現值覆蓋舊屬性
        stmt = stmt.with_for_update(of=Exam, read=for_share).execution_options(
            populate_existing=True
        )
    exam = session.execute(stmt).unique().scalar_one_or_none()
    if exam is None:
        raise NotFoundError("exam_not_found", "找不到考試")
    return exam


def get_exam(session: Session, exam_id: UUID) -> ExamOut:
    """含科目（sort_order）、應考人數、發布者姓名。"""
    return _exam_outs(session, [get_exam_or_404(session, exam_id)])[0]
