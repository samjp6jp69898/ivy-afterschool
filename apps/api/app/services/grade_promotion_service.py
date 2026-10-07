"""BACKEND-157：學年升級預覽（GradePromotionService.preview；domain_spec M3：每年 8 月升級，
grade_level +1、六年級轉 withdrawn，後台批次處理、不自動執行）。

參考 ivy ``api/students.py::bulk_graduate_students`` 的批次轉態；去掉幼稚園畢業流程與 lifecycle。

- 對象：``archived_at is null`` 且 status ∈ {active, suspended}；withdrawn 不動。
- grade 1~5 → promote（grade_to = grade + 1）；grade 6 → graduate（grade_to = None，執行時轉
  withdrawn）。依 grade、student_no 排序。
- 升級不改 class_id：PromotionItem.class_name 顯示原安親班班級，之後由行政逐一調整。
- ``is_already_promoted``：已有 audit ``student.promote_grade`` / entity_type ``academic_year`` /
  entity_id = 學年度 → preview 仍回結果並附 ``already_promoted=True``（BACKEND-158 執行前以同一
  判斷擋重複執行）。

BACKEND-158 ``execute``（執行）：
1. ``advisory_xact_lock('students:promote_grade', 學年度)``：同時按兩次的第二個請求在此等待，
   前者 commit 後走到步驟 2 得到 409。
2. 已有 ``student.promote_grade`` 稽核 → 409 ``already_promoted``。
3. 以 FOR UPDATE（依主鍵排序）鎖住全部對象列，再重算 preview：``total != expected_total``
   （或鎖到的列數與 preview 不符）→ 409 ``preview_stale``。對象列鎖住後，並發的 update_student 在
   學生列上等待，名單與寫入之間不會變動。
4. 六年級 ``enrolled_on`` 晚於退班日會撞 DB CHECK → 先以 422 ``invalid_dates`` 指出學生，整批不動。
5. 兩條 bulk update（只對鎖住的 id）：先六年級 → withdrawn / ``withdrawn_on or clock.today()``，
   再 1~5 年級 ``grade_level + 1``（順序確保剛升上六年級的不會被轉退班）；class_id 不變。
6. 畢業生依主鍵順序逐一 ``close_out_inactive_student``（鎖序與 update_student 同向：學生列 →
   授權列 → 請求列 → 請假 / 出勤）。
7. 稽核 ``student.promote_grade``（entity_type ``academic_year``、entity_id 學年度、after
   ``{promoted, graduated, to_academic_year}``）。不 commit。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Final, cast
from uuid import UUID

from sqlalchemy import CursorResult, exists, select, update
from sqlalchemy.orm import Session

from app.api.deps import CurrentStaff
from app.core.clock import Clock
from app.core.errors import AppError, ConflictError
from app.core.locks import advisory_xact_lock
from app.core.request_meta import RequestMeta
from app.models.audit import AuditLog
from app.models.students import Student
from app.services import audit_service
from app.services.student_service import close_out_inactive_student

PROMOTE_GRADE_ACTION: Final = "student.promote_grade"
PROMOTE_GRADE_ENTITY_TYPE: Final = "academic_year"
PROMOTE_LOCK_NAMESPACE: Final = "students:promote_grade"
GRADUATING_GRADE: Final = 6
_PROMOTABLE_STATUSES: Final = ("active", "suspended")


@dataclass(frozen=True)
class PromotionItem:
    id: UUID
    student_no: str
    name: str
    grade_from: int
    grade_to: int | None  # 六年級畢業（轉 withdrawn）為 None
    class_name: str | None


@dataclass(frozen=True)
class PromotionPreview:
    from_academic_year: int
    to_academic_year: int
    promote: list[PromotionItem]
    graduate: list[PromotionItem]
    total: int
    already_promoted: bool


def is_already_promoted(session: Session, from_academic_year: int) -> bool:
    return bool(
        session.execute(
            select(
                exists().where(
                    AuditLog.action == PROMOTE_GRADE_ACTION,
                    AuditLog.entity_type == PROMOTE_GRADE_ENTITY_TYPE,
                    AuditLog.entity_id == str(from_academic_year),
                )
            )
        ).scalar_one()
    )


def _candidate_filter() -> tuple[Any, ...]:
    return (Student.archived_at.is_(None), Student.status.in_(_PROMOTABLE_STATUSES))


def _candidates(session: Session) -> list[Student]:
    # Student.class_ 為 lazy='joined'，班名一次帶出
    return list(
        session.execute(
            select(Student)
            .where(*_candidate_filter())
            .order_by(Student.grade_level, Student.student_no, Student.id)
        ).scalars()
    )


def _item(student: Student) -> PromotionItem:
    graduating = student.grade_level >= GRADUATING_GRADE
    return PromotionItem(
        id=student.id,
        student_no=student.student_no,
        name=student.name,
        grade_from=student.grade_level,
        grade_to=None if graduating else student.grade_level + 1,
        class_name=student.class_.name if student.class_ is not None else None,
    )


def preview(session: Session, *, from_academic_year: int) -> PromotionPreview:
    items = [_item(student) for student in _candidates(session)]
    promote = [item for item in items if item.grade_to is not None]
    graduate = [item for item in items if item.grade_to is None]
    return PromotionPreview(
        from_academic_year=from_academic_year,
        to_academic_year=from_academic_year + 1,
        promote=promote,
        graduate=graduate,
        total=len(promote) + len(graduate),
        already_promoted=is_already_promoted(session, from_academic_year),
    )


# --- BACKEND-158：execute -------------------------------------------------------------------------


@dataclass(frozen=True)
class PromotionResult:
    promoted: int
    graduated: int


def _rowcount(result: object) -> int:
    return int(cast(CursorResult[Any], result).rowcount or 0)


def _lock_candidate_ids(session: Session) -> list[UUID]:
    """鎖住全部對象列（FOR UPDATE，依主鍵排序：批次鎖多列的全專案約定）。"""
    return list(
        session.execute(
            select(Student.id).where(*_candidate_filter()).order_by(Student.id).with_for_update()
        ).scalars()
    )


def _check_graduation_dates(session: Session, ids: list[UUID], graduation_date: date) -> None:
    """六年級 enrolled_on 晚於退班日會違反 ck_students_withdrawn_on：先指出學生，整批不動。"""
    late = session.execute(
        select(Student.id, Student.student_no, Student.name, Student.enrolled_on)
        .where(
            Student.id.in_(ids),
            Student.grade_level >= GRADUATING_GRADE,
            Student.enrolled_on > graduation_date,
        )
        .order_by(Student.student_no, Student.id)
    ).all()
    if late:
        raise AppError(
            "invalid_dates",
            "部分六年級學生的入班日晚於退班日，請先修正入班日或指定較晚的退班日",
            status=422,
            details={
                "withdrawn_on": graduation_date,
                "students": [
                    {"id": sid, "student_no": no, "name": name, "enrolled_on": enrolled}
                    for sid, no, name, enrolled in late
                ],
            },
        )


def execute(
    session: Session,
    *,
    from_academic_year: int,
    expected_total: int,
    withdrawn_on: date | None,
    actor: CurrentStaff,
    meta: RequestMeta,
    clock: Clock,
) -> PromotionResult:
    advisory_xact_lock(session, PROMOTE_LOCK_NAMESPACE, str(from_academic_year))
    if is_already_promoted(session, from_academic_year):
        raise ConflictError("already_promoted", f"{from_academic_year} 學年度的升級已經執行過")
    ids = _lock_candidate_ids(session)
    current = preview(session, from_academic_year=from_academic_year)
    if current.total != expected_total or len(ids) != current.total:
        raise ConflictError(
            "preview_stale",
            "預覽後名單有變，請重新預覽",
            details={"expected_total": expected_total, "total": current.total},
        )
    graduation_date = withdrawn_on or clock.today()
    _check_graduation_dates(session, ids, graduation_date)

    # 先六年級再其他：剛升上六年級的不會被同一次執行轉為退班。synchronize_session='fetch' 讓
    # session 內已載入的學生物件（preview 剛載入）同步 / 過期，呼叫端不會讀到舊值
    graduated_ids = list(
        session.execute(
            select(Student.id)
            .where(Student.id.in_(ids), Student.grade_level >= GRADUATING_GRADE)
            .order_by(Student.id)
        ).scalars()
    )
    if graduated_ids:
        session.execute(
            update(Student)
            .where(Student.id.in_(graduated_ids))
            .values(status="withdrawn", withdrawn_on=graduation_date)
            .execution_options(synchronize_session="fetch")
        )
    promoted = _rowcount(
        session.execute(
            update(Student)
            .where(Student.id.in_(ids), Student.grade_level < GRADUATING_GRADE)
            .values(grade_level=Student.grade_level + 1)
            .execution_options(synchronize_session="fetch")
        )
    )

    # 畢業生逐一收尾：鎖序與 update_student 同向（學生列 → 授權列 → 請求列 → 請假 / 出勤）
    for student in session.execute(
        select(Student)
        .where(Student.id.in_(graduated_ids))
        .order_by(Student.id)
        .execution_options(populate_existing=True)
    ).scalars():
        close_out_inactive_student(session, student, actor=actor, clock=clock)

    audit_service.record(
        session,
        actor=audit_service.Actor.staff(actor),
        action=PROMOTE_GRADE_ACTION,
        entity_type=PROMOTE_GRADE_ENTITY_TYPE,
        entity_id=str(from_academic_year),
        after={
            "promoted": promoted,
            "graduated": len(graduated_ids),
            "to_academic_year": from_academic_year + 1,
        },
        meta=meta,
    )
    return PromotionResult(promoted=promoted, graduated=len(graduated_ids))
