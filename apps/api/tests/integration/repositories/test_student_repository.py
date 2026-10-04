"""BACKEND-133：app/repositories/students.py（學生 / 班級共用查詢）。"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import event, text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models.classes import SchoolClass
from app.models.students import Student
from app.repositories.students import (
    StudentBrief,
    get_class_or_404,
    get_student_or_404,
    list_active_student_ids,
    student_brief_map,
)
from tests.support.factories import make_class, make_student


@contextmanager
def _count_statements(session: Session) -> Iterator[list[str]]:
    bind = session.get_bind()
    assert isinstance(bind, Connection)
    statements: list[str] = []

    def _record(conn: Connection, cursor: object, statement: str, *args: object) -> None:
        statements.append(statement)

    event.listen(bind, "before_cursor_execute", _record)
    try:
        yield statements
    finally:
        event.remove(bind, "before_cursor_execute", _record)


def _withdrawn(session: Session, *, class_: SchoolClass | None = None, grade_level: int) -> Student:
    # ck_students_withdrawn_status：withdrawn 必須有 withdrawn_on，factory 只能先建 active 再改
    student = make_student(session, class_=class_, grade_level=grade_level)
    student.status = "withdrawn"
    student.withdrawn_on = date(2026, 9, 1)
    session.flush()
    return student


def test_student_repo_get_or_404(db_session: Session) -> None:
    archived = make_student(db_session, archived=True)
    active = make_student(db_session)
    db_session.expire_all()

    with pytest.raises(AppError) as excinfo:
        get_student_or_404(db_session, archived.id)
    assert excinfo.value.status == 404
    assert excinfo.value.code == "student_not_found"
    assert excinfo.value.message == "找不到學生"

    assert get_student_or_404(db_session, archived.id, include_archived=True).id == archived.id
    assert get_student_or_404(db_session, active.id).id == active.id

    with pytest.raises(AppError) as missing:
        get_student_or_404(db_session, uuid4(), include_archived=True)
    assert (missing.value.status, missing.value.code) == (404, "student_not_found")


def test_student_repo_get_for_update_locks_row(db_session: Session) -> None:
    klass = make_class(db_session)
    student = make_student(db_session, class_=klass)
    unclassed = make_student(db_session)
    student_id, unclassed_id = student.id, unclassed.id
    db_session.expire_all()

    with _count_statements(db_session) as statements:
        got = get_student_or_404(db_session, student_id, for_update=True)
        no_class = get_student_or_404(db_session, unclassed_id, for_update=True)

    assert got.id == student.id
    assert no_class.id == unclassed.id
    # joined 的 outer join 側不可被鎖（Postgres 會拒絕），只鎖 students
    locking = [stmt for stmt in statements if "FOR UPDATE" in stmt]
    assert len(locking) == 2
    assert all("FOR UPDATE OF students" in stmt for stmt in locking)


def test_student_repo_list_active(db_session: Session) -> None:
    class_a = make_class(db_session, grade_levels=(3, 4))
    class_b = make_class(db_session, grade_levels=(3, 4))
    wanted = make_student(db_session, class_=class_a, grade_level=3)
    make_student(db_session, class_=class_a, grade_level=3, status="suspended")
    _withdrawn(db_session, class_=class_a, grade_level=3)
    make_student(db_session, class_=class_a, grade_level=3, archived=True)
    make_student(db_session, class_=class_b, grade_level=3)
    grade_four = make_student(db_session, class_=class_a, grade_level=4)

    assert set(list_active_student_ids(db_session, class_id=class_a.id)) == {
        wanted.id,
        grade_four.id,
    }
    assert list_active_student_ids(db_session, class_id=class_a.id, grade_levels=[3]) == [wanted.id]
    assert list_active_student_ids(db_session, class_id=class_a.id, grade_levels=[]) == []
    assert list_active_student_ids(db_session, class_id=uuid4()) == []


def test_student_repo_list_active_by_grade_without_class(db_session: Session) -> None:
    g6 = make_student(db_session, grade_level=6)
    _withdrawn(db_session, grade_level=6)

    ids = list_active_student_ids(db_session, grade_levels=[6])

    assert g6.id in ids
    assert len(ids) == len(set(ids))


def test_student_repo_brief_map_single_query(db_session: Session) -> None:
    klass = make_class(db_session, name="三年甲班")
    in_class = make_student(db_session, name="王小明", class_=klass, grade_level=3)
    unclassed = make_student(db_session, name="陳小華", grade_level=2, status="suspended")
    other = make_student(db_session, name="林小美", class_=klass, grade_level=4)
    ids = [in_class.id, unclassed.id, other.id]
    db_session.expire_all()

    with _count_statements(db_session) as statements:
        briefs = student_brief_map(db_session, ids)

    assert len(statements) == 1
    assert set(briefs) == {in_class.id, unclassed.id, other.id}
    assert briefs[in_class.id] == StudentBrief(
        id=in_class.id,
        student_no=in_class.student_no,
        name="王小明",
        grade_level=3,
        class_id=klass.id,
        class_name="三年甲班",
        status="active",
    )
    assert briefs[unclassed.id].class_id is None
    assert briefs[unclassed.id].class_name is None
    assert briefs[unclassed.id].status == "suspended"


def test_student_repo_brief_map_empty_and_unknown_ids(db_session: Session) -> None:
    student = make_student(db_session)

    with _count_statements(db_session) as statements:
        assert student_brief_map(db_session, []) == {}
    assert statements == []

    briefs = student_brief_map(db_session, [student.id, uuid4(), student.id])
    assert set(briefs) == {student.id}


def test_student_repo_brief_map_includes_archived(db_session: Session) -> None:
    archived = make_student(db_session, archived=True)

    assert archived.id in student_brief_map(db_session, [archived.id])


def test_student_repo_get_class_or_404(db_session: Session) -> None:
    archived = make_class(db_session, archived=True)
    active = make_class(db_session)

    with pytest.raises(AppError) as excinfo:
        get_class_or_404(db_session, archived.id)
    assert excinfo.value.status == 404
    assert excinfo.value.code == "class_not_found"

    assert get_class_or_404(db_session, archived.id, include_archived=True).id == archived.id
    assert get_class_or_404(db_session, active.id).id == active.id
    with pytest.raises(AppError) as missing:
        get_class_or_404(db_session, uuid4())
    assert missing.value.code == "class_not_found"


def test_student_repo_for_update_refreshes_loaded_instance(db_session: Session) -> None:
    student = make_student(db_session, name="王小明", grade_level=3)
    loaded = get_student_or_404(db_session, student.id)
    assert loaded.grade_level == 3

    # 另一條路徑（SQL 直接改 DB）改了值；identity map 內的舊物件不會自動更新
    db_session.execute(
        text("update students set grade_level = 4, status = 'suspended' where id = :id"),
        {"id": student.id},
    )

    locked = get_student_or_404(db_session, student.id, for_update=True)

    assert locked is loaded
    assert locked.grade_level == 4
    assert locked.status == "suspended"
