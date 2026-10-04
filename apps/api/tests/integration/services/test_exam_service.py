"""BACKEND-452 / 464 / 465 / 466：exam_service（應考名單、歷次成績、家長端成績）。"""

from datetime import date
from uuid import UUID

from sqlalchemy.orm import Session

from app.services.exam_service import resolve_exam_roster
from tests.support.factories import make_class, make_exam, make_student


def _mine(roster: list[UUID], ids: set[UUID]) -> list[UUID]:
    """只看本測試建立的學生，避免 DB 內其他資料干擾。"""
    return [sid for sid in roster if sid in ids]


def test_exam_roster_scopes(db_session: Session) -> None:
    class_a = make_class(db_session, name="A班", grade_levels=(3, 4))
    class_b = make_class(db_session, name="B班", grade_levels=(3,))
    ming = make_student(db_session, name="王小明", grade_level=3, class_=class_a)
    hua = make_student(db_session, name="陳小華", grade_level=4, class_=class_a)
    an = make_student(db_session, name="林小安", grade_level=3, class_=class_b)
    mine = {ming.id, hua.id, an.id}

    by_grade = resolve_exam_roster(db_session, make_exam(db_session, grade_level=3))
    by_class = resolve_exam_roster(
        db_session, make_exam(db_session, grade_level=None, class_=class_a)
    )
    both = resolve_exam_roster(db_session, make_exam(db_session, grade_level=3, class_=class_a))

    assert _mine(by_grade, mine) == [ming.id, an.id]
    assert by_class == [ming.id, hua.id]
    assert both == [ming.id]


def test_exam_roster_excludes_inactive(db_session: Session) -> None:
    class_a = make_class(db_session, name="A班", grade_levels=(3,))
    ming = make_student(db_session, name="王小明", grade_level=3, class_=class_a)
    # ck_students_withdrawn_status：withdrawn 必須有 withdrawn_on，factory 只能先建 active 再改
    withdrawn = make_student(db_session, name="張小美", grade_level=3, class_=class_a)
    withdrawn.status = "withdrawn"
    withdrawn.withdrawn_on = date(2026, 9, 1)
    db_session.flush()
    make_student(db_session, name="李小龍", grade_level=3, class_=class_a, status="suspended")
    make_student(db_session, name="趙小雅", grade_level=3, class_=class_a, archived=True)

    roster = resolve_exam_roster(
        db_session, make_exam(db_session, grade_level=None, class_=class_a)
    )

    assert roster == [ming.id]
