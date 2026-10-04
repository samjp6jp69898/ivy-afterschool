"""BACKEND-149：app/services/student_service.py（list_students）。
BACKEND-150：get_student（敏感欄位依權限解密、照片短效 URL、監護人清單、封存可查）。"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from datetime import date
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from app.api.deps import CurrentStaff
from app.core.config import get_settings
from app.core.crypto import derive_key, encrypt_bytes
from app.core.errors import AppError
from app.core.pagination import PageParams
from app.core.storage import StorageError
from app.models.students import Student
from app.schemas.students import StudentDetailOut, StudentListQuery
from app.services.student_service import get_student, list_students
from app.services.students.id_number import id_number_hmac, normalize_id_number
from tests.support.factories import (
    make_class,
    make_guardian,
    make_parent,
    make_school,
    make_student,
)
from tests.support.fake_clock import FakeClock
from tests.support.fake_storage import FakeStorage

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

    assert set(_names(db_session, actor, q="s1150")) == {"王小明", "陳小華"}
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
    assert _names(db_session, with_perm, q="\uff41123456789") == ["王小明"]
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

    assert set(_names(db_session, actor, class_id=str(klass.id))) == {"甲", "丙"}
    assert _names(db_session, actor, class_id=str(klass.id), status="withdrawn") == ["丙"]
    assert _names(db_session, actor, school_id=str(school.id)) == ["甲"]
    assert set(_names(db_session, actor, class_id=str(klass.id), grade_level=3)) == {"甲", "丙"}
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


# --- BACKEND-150：get_student --------------------------------------------------------------------


def _get(
    db: Session, student_id: UUID, actor: CurrentStaff, clock: FakeClock, storage: FakeStorage
) -> StudentDetailOut:
    return get_student(db, student_id, actor=actor, storage=storage, clock=clock)


def _with_sensitive(db: Session) -> Student:
    student = make_student(db, name="王小明")
    student.id_number_enc = encrypt_bytes("A123456789")
    student.id_number_hmac = id_number_hmac("A123456789")
    student.health_note_enc = encrypt_bytes("對花生過敏")
    db.flush()
    return student


def test_get_student_sensitive_visible(db_session: Session, fake_clock: FakeClock) -> None:
    student = _with_sensitive(db_session)

    out = _get(
        db_session,
        student.id,
        _actor("students:read", "students:sensitive"),
        fake_clock,
        FakeStorage(),
    )

    assert isinstance(out, StudentDetailOut)
    assert out.id == student.id
    assert out.name == "王小明"
    assert out.has_id_number is True
    assert out.has_health_note is True
    assert out.sensitive is not None
    assert out.sensitive.model_dump() == {"id_number": "A123456789", "health_note": "對花生過敏"}
    assert out.photo_url is None
    assert out.guardians == []


def test_get_student_sensitive_hidden(db_session: Session, fake_clock: FakeClock) -> None:
    student = _with_sensitive(db_session)

    out = _get(db_session, student.id, _actor("students:read"), fake_clock, FakeStorage())

    assert out.sensitive is None
    assert out.has_id_number is True
    assert out.has_health_note is True
    dumped = json.dumps(out.model_dump(mode="json"), ensure_ascii=False)
    assert "A123456789" not in dumped
    assert "對花生過敏" not in dumped
    assert "id_number_enc" not in dumped
    # 沒有敏感資料的學生：has_* 皆 False
    plain = make_student(db_session, name="陳小華")
    out_plain = _get(
        db_session,
        plain.id,
        _actor("students:read", "students:sensitive"),
        fake_clock,
        FakeStorage(),
    )
    assert out_plain.has_id_number is False
    assert out_plain.has_health_note is False
    assert out_plain.sensitive is not None
    assert out_plain.sensitive.model_dump() == {"id_number": None, "health_note": None}


def test_get_student_decrypt_failure_logged(
    db_session: Session, fake_clock: FakeClock, caplog: pytest.LogCaptureFixture
) -> None:
    student = _with_sensitive(db_session)
    student.id_number_enc = b"\x01garbage"
    db_session.flush()

    with caplog.at_level(logging.ERROR, logger="app.services.student_service"):
        out = _get(db_session, student.id, _actor("students:sensitive"), fake_clock, FakeStorage())

    assert out.sensitive is not None
    assert out.sensitive.id_number is None
    assert out.sensitive.health_note == "對花生過敏"
    assert out.has_id_number is True
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert any(str(student.id) in r.getMessage() for r in errors)
    assert all("garbage" not in r.getMessage() for r in caplog.records)


def test_get_student_photo_url(db_session: Session, fake_clock: FakeClock) -> None:
    student = make_student(db_session)
    student.photo_path = f"{student.id}/{'ab' * 16}.jpg"
    db_session.flush()
    storage = FakeStorage()

    out = _get(db_session, student.id, _actor("students:read"), fake_clock, storage)
    assert out.photo_url == (
        f"https://storage.test/student-photos/{student.id}/{'ab' * 16}.jpg?exp=300"
    )

    storage.sign_error = StorageError("簽名失敗")
    out = _get(db_session, student.id, _actor("students:read"), fake_clock, storage)
    assert out.photo_url is None
    assert out.id == student.id


def test_get_student_guardians_and_archived(db_session: Session, fake_clock: FakeClock) -> None:
    klass = make_class(db_session, name="彩虹班")
    school = make_school(db_session)
    student = make_student(db_session, class_=klass, school=school, archived=True)
    parent = make_parent(db_session, display_name="王媽媽")
    bound = make_guardian(db_session, student, parent=parent, name="王媽媽", is_primary=True)
    unbound = make_guardian(db_session, student, name="王爸爸", relation="father")
    make_guardian(db_session, student, name="王阿嬤", relation="grandparent", archived=True)

    out = _get(db_session, student.id, _actor("students:read"), fake_clock, FakeStorage())

    assert out.archived_at is not None
    assert out.class_ is not None
    assert out.class_.name == "彩虹班"
    assert out.school is not None
    assert out.school.id == school.id
    assert [g.id for g in out.guardians] == [bound.id, unbound.id]
    assert out.guardians[0].binding.status == "bound"
    assert out.guardians[0].binding.parent_display_name == "王媽媽"
    assert out.guardians[1].binding.status == "unbound"


def test_get_student_not_found(db_session: Session, fake_clock: FakeClock) -> None:
    with pytest.raises(AppError) as exc:
        _get(db_session, uuid4(), _actor("students:read"), fake_clock, FakeStorage())

    assert (exc.value.status, exc.value.code) == (404, "student_not_found")
