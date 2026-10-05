"""成績模組 service（domain_spec M8）。

- BACKEND-452：``resolve_exam_roster``。
- BACKEND-464：``get_student_exam_history``（後台個人歷次成績，含 draft）。
- BACKEND-465 / 466：家長端成績列表 / 明細，呼叫端已驗證所有權；只看 ``published`` 且該生有成績列
  （含缺考）的考試，草稿、無成績列與不存在的考試一律同一個 404 ``exam_not_found``。
- BACKEND-453：``list_exams``（後台考試列表，篩選 + 分頁）。
- BACKEND-454：``get_exam`` / ``get_exam_or_404``（考試詳情；本模組其他方法共用的取得與鎖定）。
- BACKEND-455 / 457：``create_exam`` / ``delete_exam``（只能刪草稿）。
- BACKEND-458：``set_exam_subjects``（以清單取代科目設定；移除科目連帶刪成績）。
- BACKEND-459：``get_score_grid``（成績輸入格：學生對科目）。
- BACKEND-462：``unpublish_exam``（取消發布，寫 audit）。
- BACKEND-463：``get_exam_summary``（各科平均，不做排名）。
- BACKEND-456：``update_exam``（已發布只可改名稱、日期、備註）。
- BACKEND-460：``upsert_scores``（批次登分；發布後修改寫 audit、可選重新通知）。
- BACKEND-461：``publish_exam``（條件式發布、通知應考家長）。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from typing import TYPE_CHECKING, Final, cast
from uuid import UUID

from sqlalchemy import ColumnElement, Select, and_, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, defaultload

from app.core.errors import AppError, ConflictError, NotFoundError
from app.core.pagination import Page, PageParams, paginate
from app.models.account import StaffUser
from app.models.classes import SchoolClass
from app.models.exams import Exam, ExamScore, ExamSubject
from app.models.reference import ExamType, Subject
from app.models.students import Student
from app.notifications.events import Event
from app.notifications.recipients import parent_recipients_bulk
from app.notifications.service import enqueue
from app.repositories.students import get_student_or_404, list_active_student_ids
from app.schemas.classes import ClassBriefOut
from app.schemas.exams import (
    ExamCreateIn,
    ExamListQuery,
    ExamOut,
    ExamSubjectOut,
    ExamSubjectsPutIn,
    ExamSummaryOut,
    ExamTypeBriefOut,
    ExamUpdateIn,
    GridStudentOut,
    HistoryScoreOut,
    ParentExamDetailOut,
    ParentExamListItemOut,
    ParentExamSubjectScoreOut,
    ScoreCellOut,
    ScoreGridOut,
    ScoresPutIn,
    ScoresPutOut,
    StudentExamHistoryItemOut,
    SubjectSummaryOut,
)
from app.services.audit_service import Actor, record

if TYPE_CHECKING:
    from app.api.deps import CurrentStaff
    from app.core.clock import Clock
    from app.core.request_meta import RequestMeta

# DB-028 下修滿分低於既有分數時 trigger 回報的約束名稱（SQLSTATE 23514）
FULL_SCORE_FLOOR_CONSTRAINT: Final = "ck_exam_subjects_full_score_floor"
# DB-028 分數超過滿分時 trigger 回報的約束名稱（與滿分下修競態時才會遇到）
SCORE_WITHIN_FULL_CONSTRAINT: Final = "ck_exam_scores_within_full_score"
_SCOPE_FIELDS: Final = frozenset({"exam_type_id", "grade_level", "class_id"})


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


def _exam_outs(
    session: Session, exams: list[Exam], *, roster: dict[UUID, int] | None = None
) -> list[ExamOut]:
    """應考人數與發布者姓名各以一次查詢批次解析；呼叫端已算好應考人數時以 roster 傳入。"""
    if roster is None:
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


def _student_in_roster(exam: Exam) -> ColumnElement[bool]:
    """與 resolve_exam_roster 相同的名單條件（以該考試目前的 class_id / grade_level 為準）。"""
    conditions = [Student.status == "active", Student.archived_at.is_(None)]
    if exam.class_id is not None:
        conditions.append(Student.class_id == exam.class_id)
    if exam.grade_level is not None:
        conditions.append(Student.grade_level == exam.grade_level)
    return and_(*conditions)


def _require_draft(exam: Exam) -> None:
    if exam.status == "published":
        raise ConflictError(
            "exam_published", "考試已發布，請先取消發布再修改", details={"exam_id": exam.id}
        )


def _check_exam_type(session: Session, exam_type_id: UUID) -> None:
    exam_type = session.get(ExamType, exam_type_id)
    if exam_type is None or not exam_type.is_active:
        raise AppError("invalid_exam_type", "考試類型不存在或已停用", status=422)


def _check_class_scope(session: Session, class_id: UUID, grade_level: int | None) -> None:
    school_class = session.execute(
        select(SchoolClass).where(SchoolClass.id == class_id, SchoolClass.archived_at.is_(None))
    ).scalar_one_or_none()
    if school_class is None:
        raise AppError("invalid_class", "班級不存在或已封存", status=422)
    # 班級不含該年級時名單必為空，視為輸入錯誤
    if grade_level is not None and grade_level not in school_class.grade_levels:
        raise AppError(
            "grade_not_in_class",
            "班級不包含指定的年級",
            status=422,
            details={"grade_levels": school_class.grade_levels},
        )


def create_exam(session: Session, data: ExamCreateIn, *, actor: CurrentStaff) -> ExamOut:
    """建立為 draft、不含科目（科目以 set_exam_subjects 設定）。"""
    _check_exam_type(session, data.exam_type_id)
    if data.class_id is not None:
        _check_class_scope(session, data.class_id, data.grade_level)

    exam = Exam(
        name=data.name,
        exam_type_id=data.exam_type_id,
        exam_date=data.exam_date,
        grade_level=data.grade_level,
        class_id=data.class_id,
        note=data.note,
        status="draft",
    )
    session.add(exam)
    session.flush()
    return get_exam(session, exam.id)


def delete_exam(session: Session, exam_id: UUID, *, actor: CurrentStaff) -> None:
    """只能刪草稿；exam_subjects / exam_scores 由 FK cascade 一併刪除。"""
    exam = get_exam_or_404(session, exam_id, for_update=True)
    _require_draft(exam)
    # ORM 沒有設定 cascade：以 DELETE 交給 DB 的 on delete cascade，再把物件移出 session
    session.execute(delete(Exam).where(Exam.id == exam_id))
    session.expunge(exam)


def _max_scores(session: Session, exam_id: UUID) -> dict[UUID, float]:
    rows = session.execute(
        select(ExamScore.subject_id, func.max(ExamScore.score))
        .where(ExamScore.exam_id == exam_id, ExamScore.score.is_not(None))
        .group_by(ExamScore.subject_id)
    )
    return {subject_id: float(max_score) for subject_id, max_score in rows if max_score is not None}


def set_exam_subjects(
    session: Session, exam_id: UUID, data: ExamSubjectsPutIn, *, actor: CurrentStaff
) -> ExamOut:
    """以 items 取代現有設定：移除的科目連同成績刪除（複合 FK cascade）、既有科目更新、新科目新增。

    下修滿分低於既有分數由 DB trigger 擋下（23514），以 savepoint 捕捉後回 409 並指出科目與最高分。
    """
    exam = get_exam_or_404(session, exam_id, for_update=True)
    _require_draft(exam)

    wanted = {item.subject_id: item for item in data.items}
    active = set(
        session.execute(
            select(Subject.id).where(Subject.id.in_(wanted), Subject.is_active.is_(True))
        ).scalars()
    )
    invalid = sorted(str(subject_id) for subject_id in wanted if subject_id not in active)
    if invalid:
        raise AppError(
            "invalid_subject", "科目不存在或已停用", status=422, details={"subject_ids": invalid}
        )

    try:
        with session.begin_nested():
            existing = {subject.subject_id: subject for subject in exam.subjects}
            for subject_id, subject in existing.items():
                if subject_id not in wanted:
                    session.delete(subject)
            for subject_id, item in wanted.items():
                current = existing.get(subject_id)
                if current is None:
                    session.add(
                        ExamSubject(
                            exam_id=exam.id,
                            subject_id=subject_id,
                            full_score=item.full_score,
                            sort_order=item.sort_order,
                        )
                    )
                else:
                    current.full_score = item.full_score
                    current.sort_order = item.sort_order
            session.flush()
    except IntegrityError as exc:
        diag = getattr(exc.orig, "diag", None)
        if getattr(diag, "constraint_name", None) != FULL_SCORE_FLOOR_CONSTRAINT:
            raise
        max_scores = _max_scores(session, exam.id)
        for subject_id, item in wanted.items():
            highest = max_scores.get(subject_id)
            if highest is not None and highest > item.full_score:
                raise ConflictError(
                    "full_score_below_existing",
                    "滿分不可低於已登記的最高分",
                    details={"subject_id": subject_id, "max_score": highest},
                ) from exc
        raise

    session.expire(exam, ["subjects"])
    return _exam_outs(session, [exam])[0]


def get_score_grid(session: Session, exam_id: UUID) -> ScoreGridOut:
    """學生 = 目前名單（in_roster）加上已有成績列但不在名單的學生；依班名、學號排序。

    cells 只回既有成績列（未登分的格子前端視為空白）。SQL 固定：考試、科目、學生（含名單判斷）、
    成績列、發布者姓名（已發布時）。
    """
    exam = get_exam_or_404(session, exam_id)
    in_roster = _student_in_roster(exam)
    has_score = Student.id.in_(select(ExamScore.student_id).where(ExamScore.exam_id == exam.id))
    students = session.execute(
        select(
            Student.id,
            Student.student_no,
            Student.name,
            SchoolClass.name,
            in_roster.label("in_roster"),
        )
        .outerjoin(SchoolClass, SchoolClass.id == Student.class_id)
        .where(or_(in_roster, has_score))
    ).all()
    grid_students = sorted(
        (
            GridStudentOut(
                id=student_id,
                student_no=student_no,
                name=name,
                class_name=class_name,
                in_roster=bool(roster_flag),
            )
            for student_id, student_no, name, class_name, roster_flag in students
        ),
        key=lambda s: (s.class_name is None, s.class_name or "", s.student_no),
    )
    scores = session.execute(
        select(ExamScore).where(ExamScore.exam_id == exam.id).order_by(ExamScore.id)
    ).scalars()
    exam_out = _exam_outs(
        session, [exam], roster={exam.id: sum(1 for s in grid_students if s.in_roster)}
    )[0]
    return ScoreGridOut(
        exam=exam_out,
        students=grid_students,
        subjects=exam_out.subjects,
        cells=[
            ScoreCellOut(
                student_id=score.student_id,
                subject_id=score.subject_id,
                score=score.score,
                is_absent=score.is_absent,
                note=score.note,
                updated_at=score.updated_at,
            )
            for score in scores
        ],
    )


def unpublish_exam(
    session: Session, exam_id: UUID, *, actor: CurrentStaff, meta: RequestMeta, clock: Clock
) -> ExamOut:
    """published → draft（清空 published_at / by）並寫 audit；已送出的通知無法收回。"""
    exam = get_exam_or_404(session, exam_id, for_update=True)
    published_at = exam.published_at
    hit = session.execute(
        update(Exam)
        .where(Exam.id == exam_id, Exam.status == "published")
        .values(status="draft", published_at=None, published_by=None)
        .returning(Exam.id)
    ).scalar_one_or_none()
    if hit is None:
        raise ConflictError("exam_not_published", "考試尚未發布")
    record(
        session,
        actor=Actor.staff(actor),
        action="exam.unpublish",
        entity_type="exam",
        entity_id=exam_id,
        before={"status": "published", "published_at": published_at},
        after={"status": "draft"},
        meta=meta,
    )
    # bulk update 不經 ORM：以 DB 現值刷新已載入的物件
    session.refresh(exam)
    return _exam_outs(session, [exam])[0]


def get_exam_summary(session: Session, exam_id: UUID) -> ExamSummaryOut:
    """每科 scored / absent / missing 計數與平均（2 位小數）、最高、最低；缺考與未登分不計入平均。

    成績統計為單一 GROUP BY 查詢。
    """
    exam = get_exam_or_404(session, exam_id)
    roster_count = _roster_counts(session, [exam.id]).get(exam.id, 0)
    rows = session.execute(
        select(
            ExamScore.subject_id,
            func.count(ExamScore.score),
            func.count().filter(ExamScore.is_absent.is_(True)),
            func.count(),
            func.round(func.avg(ExamScore.score), 2),
            func.max(ExamScore.score),
            func.min(ExamScore.score),
        )
        .where(ExamScore.exam_id == exam.id)
        .group_by(ExamScore.subject_id)
    )
    stats = {row[0]: row[1:] for row in rows}
    subjects = []
    for subject in exam.subjects:
        scored, absent, with_row, average, highest, lowest = stats.get(
            subject.subject_id, (0, 0, 0, None, None, None)
        )
        subjects.append(
            SubjectSummaryOut(
                subject_id=subject.subject_id,
                subject_name=subject.subject.name,
                full_score=subject.full_score,
                scored_count=scored,
                absent_count=absent,
                missing_count=max(roster_count - with_row, 0),
                average=average,
                max=highest,
                min=lowest,
            )
        )
    return ExamSummaryOut(exam_id=exam.id, roster_count=roster_count, subjects=subjects)


def update_exam(
    session: Session, exam_id: UUID, data: ExamUpdateIn, *, actor: CurrentStaff
) -> ExamOut:
    """部分更新；已發布的考試只可改名稱 / 日期 / 備註（名單與類型在發布後鎖定）。

    範圍變更導致部分既有成績的學生不在新名單 → 允許（成績保留，格狀表以 in_roster=false 呈現）。
    """
    exam = get_exam_or_404(session, exam_id, for_update=True)
    changes = {
        name: getattr(data, name)
        for name in data.model_fields_set
        if getattr(data, name) != getattr(exam, name)
    }
    if exam.status == "published" and changes.keys() & _SCOPE_FIELDS:
        raise ConflictError(
            "exam_published",
            "考試已發布，名單與類型不可修改，請先取消發布",
            details={"exam_id": exam.id},
        )
    grade_level = changes.get("grade_level", exam.grade_level)
    class_id = changes.get("class_id", exam.class_id)
    if grade_level is None and class_id is None:
        raise AppError("exam_scope_required", "年級與班級至少要指定一個", status=422)
    if "exam_type_id" in changes:
        _check_exam_type(session, changes["exam_type_id"])
    if class_id is not None and changes.keys() & {"class_id", "grade_level"}:
        _check_class_scope(session, class_id, grade_level)

    for name, value in changes.items():
        setattr(exam, name, value)
    session.flush()
    # class_id / exam_type_id 改了：關聯物件以新值重新載入
    session.expire(exam, ["exam_type", "school_class"])
    return _exam_outs(session, [exam])[0]


def _cell_error(student_id: UUID, subject_id: UUID, code: str) -> dict[str, object]:
    return {"student_id": student_id, "subject_id": subject_id, "code": code}


def _invalid_cells(errors: list[dict[str, object]]) -> AppError:
    return AppError(
        "invalid_score_cells", "部分成績格不正確，未儲存任何格子", status=422, details=errors
    )


def _validate_cells(session: Session, exam: Exam, data: ScoresPutIn) -> None:
    """逐格驗證，全部驗完才回錯（不部分寫入）。"""
    full_scores = {subject.subject_id: subject.full_score for subject in exam.subjects}
    roster = set(resolve_exam_roster(session, exam))
    scored = set(
        session.execute(
            select(ExamScore.student_id).where(ExamScore.exam_id == exam.id).distinct()
        ).scalars()
    )
    errors: list[dict[str, object]] = []
    seen: set[tuple[UUID, UUID]] = set()
    for cell in data.cells:
        key = (cell.student_id, cell.subject_id)
        if key in seen:
            errors.append(_cell_error(*key, "duplicate_cell"))
            continue
        seen.add(key)
        full_score = full_scores.get(cell.subject_id)
        if full_score is None:
            errors.append(_cell_error(*key, "subject_not_in_exam"))
        elif cell.score is not None and cell.score > full_score:
            errors.append(_cell_error(*key, "score_out_of_range"))
        if cell.student_id not in roster and cell.student_id not in scored:
            errors.append(_cell_error(*key, "student_not_in_exam"))
        if cell.is_absent and cell.score is not None:
            errors.append(_cell_error(*key, "absent_with_score"))
    if errors:
        raise _invalid_cells(errors)


def upsert_scores(
    session: Session,
    exam_id: UUID,
    data: ScoresPutIn,
    *,
    actor: CurrentStaff,
    meta: RequestMeta,
    clock: Clock,
) -> ScoresPutOut:
    """逐格自動儲存的批次 upsert（單一語句）；發布後修改的格子寫 audit，可選擇重新通知家長。

    以 FOR SHARE 鎖考試列，與 publish / unpublish 的 FOR UPDATE 互斥：「是否屬於發布後修改」的判斷
    與實際狀態一致。同一格由後寫入者生效；draft 不寫 audit、忽略 notify_parents。
    """
    exam = get_exam_or_404(session, exam_id, for_share=True)
    _validate_cells(session, exam, data)
    published = exam.status == "published"
    keys = [(cell.student_id, cell.subject_id) for cell in data.cells]

    before: dict[tuple[UUID, UUID], dict[str, object]] = {}
    if published:
        rows = session.execute(
            select(ExamScore).where(
                ExamScore.exam_id == exam.id,
                ExamScore.student_id.in_({k[0] for k in keys}),
                ExamScore.subject_id.in_({k[1] for k in keys}),
            )
        ).scalars()
        before = {
            (row.student_id, row.subject_id): {
                "score": row.score,
                "is_absent": row.is_absent,
                "note": row.note,
            }
            for row in rows
        }

    insert = pg_insert(ExamScore).values(
        [
            {
                "exam_id": exam.id,
                "student_id": cell.student_id,
                "subject_id": cell.subject_id,
                "score": cell.score,
                "is_absent": cell.is_absent,
                "note": cell.note,
                "updated_by": actor.id,
            }
            for cell in data.cells
        ]
    )
    upsert = insert.on_conflict_do_update(
        index_elements=["exam_id", "student_id", "subject_id"],
        set_={
            "score": insert.excluded.score,
            "is_absent": insert.excluded.is_absent,
            "note": insert.excluded.note,
            "updated_by": insert.excluded.updated_by,
        },
    ).returning(
        ExamScore.student_id,
        ExamScore.subject_id,
        ExamScore.score,
        ExamScore.is_absent,
        ExamScore.note,
    )
    try:
        with session.begin_nested():
            written = session.execute(upsert).all()
    except IntegrityError as exc:
        diag = getattr(exc.orig, "diag", None)
        if getattr(diag, "constraint_name", None) != SCORE_WITHIN_FULL_CONSTRAINT:
            raise
        # 驗證後滿分被同時下修：以 DB 現值重新指出超出的格子
        current = {
            subject_id: full_score
            for subject_id, full_score in session.execute(
                select(ExamSubject.subject_id, ExamSubject.full_score).where(
                    ExamSubject.exam_id == exam.id
                )
            )
        }
        raise _invalid_cells(
            [
                _cell_error(cell.student_id, cell.subject_id, "score_out_of_range")
                for cell in data.cells
                if cell.score is not None and cell.score > current.get(cell.subject_id, cell.score)
            ]
        ) from exc

    if not published:
        return ScoresPutOut(written=len(written), changed=len(written), renotified_students=0)

    changed_students: list[UUID] = []
    changed = 0
    for student_id, subject_id, score, is_absent, note in written:
        after = {"score": score, "is_absent": is_absent, "note": note}
        previous = before.get((student_id, subject_id))
        if previous == after:
            continue
        changed += 1
        if student_id not in changed_students:
            changed_students.append(student_id)
        record(
            session,
            actor=Actor.staff(actor),
            action="exam_score.update",
            entity_type="exam_score",
            entity_id=f"{exam.id}:{student_id}:{subject_id}",
            before=previous,
            after=after,
            meta=meta,
        )
    renotified = 0
    if data.notify_parents and changed_students:
        renotified = _notify_exam_published(session, exam, changed_students, clock=clock)
    return ScoresPutOut(written=len(written), changed=changed, renotified_students=renotified)


def _notify_exam_published(
    session: Session, exam: Exam, student_ids: list[UUID], *, clock: Clock
) -> int:
    """每位學生一則 exam.published（收件人一次查詢）；沒有家長的學生略過。回傳發出通知的學生數。"""
    recipients = parent_recipients_bulk(session, student_ids)
    names = {
        student_id: name
        for student_id, name in session.execute(
            select(Student.id, Student.name).where(Student.id.in_(student_ids))
        )
    }
    notified = 0
    for student_id in student_ids:
        if not recipients.get(student_id):
            continue
        enqueue(
            session,
            Event.EXAM_PUBLISHED,
            recipients=recipients[student_id],
            payload={
                "student_id": student_id,
                "student_name": names[student_id],
                "exam_id": exam.id,
                "exam_name": exam.name,
            },
            clock=clock,
        )
        notified += 1
    return notified


def publish_exam(session: Session, exam_id: UUID, *, actor: CurrentStaff, clock: Clock) -> ExamOut:
    """draft → published（條件式更新：兩人同時按只有一人成功、只發一次通知）；通知應考家長。

    通知對象 = 應考名單加上有成績列的學生，每位學生一則。
    """
    exam = get_exam_or_404(session, exam_id, for_update=True)
    if not exam.subjects:
        raise AppError("exam_has_no_subjects", "考試尚未設定科目，無法發布", status=422)
    hit = session.execute(
        update(Exam)
        .where(Exam.id == exam_id, Exam.status == "draft")
        .values(status="published", published_at=clock.now(), published_by=actor.id)
        .returning(Exam.id)
    ).scalar_one_or_none()
    if hit is None:
        raise ConflictError("exam_already_published", "考試已發布")
    # bulk update 不經 ORM：以 DB 現值刷新
    session.refresh(exam)

    scored = session.execute(
        select(ExamScore.student_id).where(ExamScore.exam_id == exam.id).distinct()
    ).scalars()
    student_ids = list(dict.fromkeys([*resolve_exam_roster(session, exam), *scored]))
    if student_ids:
        _notify_exam_published(session, exam, student_ids, clock=clock)
    return _exam_outs(session, [exam])[0]
