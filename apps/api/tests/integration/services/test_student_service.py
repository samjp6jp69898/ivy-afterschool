"""BACKEND-149：app/services/student_service.py（list_students）。"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from app.api.deps import CurrentStaff
from app.core.config import get_settings
from app.core.crypto import derive_key
from app.core.pagination import PageParams
from app.models.students import Student
from app.schemas.students import StudentListQuery
from app.services.student_service import list_students
from app.services.students.id_number import id_number_hmac, normalize_id_number
from tests.support.factories import make_class, make_school, make_student

_PAGE = PageParams(page=1, page_size=50)


@pytest.fixture(autouse=True)
def _crypto_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@127.0.0.1:54342/postgres")
    monkeypatch.setenv("APP_SECRET_KEY", "s" * 48)
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://127.0.0.1:5341")
    monkeypatch.setenv("R2_ENDPOINT_URL", "http://127.0.0.1:54344")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "afterschool")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "afterschool-local-secret")
    monkeypatch.setenv("R2_BUCKET", "afterschool-local")
    get_settings.cache_clear()
    derive_key.cache_clear()
    yield
    get_settings.cache_clear()
    derive_key.cache_clear()


def _actor(*permissions: str) -> CurrentStaff:
    return CurrentStaff(
        id=uuid4(),
        username="staff",
        display_name="林老師",
        role_id=uuid4(),
        role_code="clerk",
        role_name="行政",
        permissions=frozenset(permissions),
        must_change_password=False,
        token_version=0,
    )


def _names(db: Session, actor: CurrentStaff, **query: object) -> list[str]:
    page = list_students(db, StudentListQuery.model_validate(query), _PAGE, actor=actor)
    return [i.name for i in page.items]


def test_list_students_search(db_session: Session) -> None:
    make_student(db_session, name="王小明", student_no="S115001")
    make_student(db_session, name="陳小華", student_no="S115002")
    make_student(db_session, name="林小美", student_no="X999")
    actor = _actor("students:read")

    assert sorted(_names(db_session, actor, q="s1150")) == ["王小明", "陳小華"]
    assert _names(db_session, actor, q="小華") == ["陳小華"]
    # 學號只比前綴，不比中段
    assert _names(db_session, actor, q="115001") == []
    # 萬用字元是字面字元，不會變成全部符合
    assert _names(db_session, actor, q="%") == []
    assert _names(db_session, actor, q="_") == []


def test_list_students_id_number_search_requires_sensitive(db_session: Session) -> None:
    student = make_student(db_session, name="王小明")
    student.id_number_enc = b"\x00enc"
    student.id_number_hmac = id_number_hmac(normalize_id_number("A123456789"))
    db_session.flush()

    with_perm = _actor("students:read", "students:sensitive")
    without_perm = _actor("students:read")

    assert _names(db_session, with_perm, q="a123456789") == ["王小明"]
    assert _names(db_session, with_perm, q=" ａ123456789 ") == ["王小明"]
    assert _names(db_session, without_perm, q="a123456789") == []
    # 非身分證格式不觸發 HMAC 比對
    assert _names(db_session, with_perm, q="A123456780") == []


def test_list_students_filters(db_session: Session) -> None:
    klass = make_class(db_session)
    school = make_school(db_session)
    in_class = make_student(db_session, name="甲", class_=klass, grade_level=3, school=school)
    make_student(db_session, name="乙", grade_level=4)
    gone = make_student(db_session, name="丙", class_=klass, grade_level=3)
    gone.status = "withdrawn"
    gone.withdrawn_on = date(2026, 9, 1)
    make_student(db_session, name="丁", class_=klass, grade_level=3, archived=True)
    db_session.flush()
    actor = _actor("students:read")

    assert sorted(_names(db_session, actor, class_id=str(klass.id))) == ["甲", "丙"]
    assert _names(db_session, actor, class_id=str(klass.id), status="withdrawn") == ["丙"]
    assert _names(db_session, actor, school_id=str(school.id)) == ["甲"]
    assert sorted(_names(db_session, actor, class_id=str(klass.id), grade_level=3)) == ["甲", "丙"]
    assert _names(db_session, actor, class_id=str(klass.id), grade_level=4) == []
    # 封存預設不出現
    assert "丁" not in _names(db_session, actor, class_id=str(klass.id))
    assert "丁" in _names(db_session, actor, class_id=str(klass.id), include_archived=True)
    item = list_students(
        db_session, StudentListQuery(class_id=klass.id, school_id=school.id), _PAGE, actor=actor
    ).items[0]
    assert item.id == in_class.id
    assert item.class_ is not None
    assert item.class_.name == klass.name
    assert item.school is not None
    assert item.school.id == school.id


def test_list_students_page(db_session: Session) -> None:
    klass = make_class(db_session)
    for n in range(25):
        make_student(db_session, class_=klass, student_no=f"P{n:03d}")
    actor = _actor("students:read")

    page = list_students(
        db_session,
        StudentListQuery(class_id=klass.id),
        PageParams(page=3, page_size=10),
        actor=actor,
    )

    assert page.total == 25
    assert [i.student_no for i in page.items] == [f"P{n:03d}" for n in range(20, 25)]


def test_list_students_order(db_session: Session) -> None:
    class_a = make_class(db_session, name="A班")
    class_b = make_class(db_session, name="B班")
    rows: list[Student] = [
        make_student(db_session, name="s4", grade_level=4, class_=class_a, student_no="Z1"),
        make_student(db_session, name="s3b", grade_level=3, class_=class_b, student_no="A1"),
        make_student(db_session, name="s3a2", grade_level=3, class_=class_a, student_no="B2"),
        make_student(db_session, name="s3a1", grade_level=3, class_=class_a, student_no="B1"),
        make_student(db_session, name="s3none", grade_level=3, student_no="A0"),
    ]
    ids: set[UUID] = {r.id for r in rows}

    page = list_students(
        db_session,
        StudentListQuery(),
        PageParams(page=1, page_size=200),
        actor=_actor("students:read"),
    )

    mine = [i.name for i in page.items if i.id in ids]
    assert mine == ["s3a1", "s3a2", "s3b", "s3none", "s4"]
