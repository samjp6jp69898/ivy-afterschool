"""BACKEND-149：app/services/student_service.py（list_students）。
BACKEND-150：get_student（敏感欄位依權限解密、照片短效 URL、監護人清單、封存可查）。
BACKEND-151：create_student（學號唯一、身分證查重含封存、敏感欄位加密與權限、稽核）。
BACKEND-153：archive_student（封存、刪未使用綁定碼、家長可見範圍排除、冪等）。
BACKEND-154：upload_photo（驗證、Storage、commit 後刪舊檔、rollback 不刪）。
BACKEND-530：purge_student（永久刪除 = 匿名化；前置條件、個資清除、統計保留、Storage、稽核）。
BACKEND-529：close_out_inactive_student（停讀 / 退班收尾：接送請求、代理授權、出勤、請假、稽核、
同交易）。"""

from __future__ import annotations

import io
import json
import logging
import re
import threading
from collections.abc import Iterator
from datetime import date, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session
from starlette.datastructures import Headers, UploadFile

from app.api.deps import CurrentStaff
from app.core.config import get_settings
from app.core.crypto import decrypt_bytes, derive_key, encrypt_bytes
from app.core.errors import AppError
from app.core.pagination import PageParams
from app.core.request_meta import RequestMeta
from app.core.storage import StorageError, build_object_path
from app.core.tx_hooks import install_tx_hooks
from app.core.uploads import PHOTO_MAX_BYTES
from app.models.attendance import StudentAttendance
from app.models.audit import AuditLog
from app.models.exams import ExamScore
from app.models.leaves import StudentLeave, StudentLeaveAttachment
from app.models.notifications import Notification
from app.models.parents import Guardian, ParentAccount, ParentBindingCode
from app.models.pickup import PickupAuthorization, PickupPerson, PickupRequest
from app.models.reference import Subject
from app.models.students import Student
from app.schemas.students import (
    StudentCreateIn,
    StudentDetailOut,
    StudentListQuery,
    StudentPurgeIn,
    StudentPurgeOut,
)
from app.services.binding_code_service import hash_code
from app.services.class_service import archive_class
from app.services.parent_scope import get_parent_student_ids
from app.services.student_service import (
    CloseOutResult,
    archive_student,
    close_out_inactive_student,
    create_student,
    get_student,
    list_students,
    purge_student,
    upload_photo,
)
from app.services.students.id_number import id_number_hmac, normalize_id_number
from tests.integration.db.conftest import connect_owner
from tests.support.factories import (
    make_attendance,
    make_class,
    make_exam,
    make_exam_score,
    make_exam_subject,
    make_guardian,
    make_leave,
    make_leave_attachment,
    make_parent,
    make_pickup_authorization,
    make_pickup_person,
    make_pickup_request,
    make_school,
    make_staff,
    make_student,
)
from tests.support.fake_clock import FakeClock
from tests.support.fake_storage import FakeStorage

_PAGE = PageParams(page=1, page_size=50)
_META = RequestMeta(ip="127.0.0.1", user_agent="pytest", request_id="req-1")
_ID_NUMBER = "A123456789"
_JPEG = b"\xff\xd8\xff\xe0" + b"0" * 100
_PDF = b"%PDF-1.7\n" + b"0" * 50
_PHOTO_URL_PREFIX = "https://storage.test/student-photos/"


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


# --- BACKEND-150：get_student ------------------------------------------------------------------


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


# --- BACKEND-151：create_student -----------------------------------------------------------------


def _create(
    db: Session, data: StudentCreateIn, actor: CurrentStaff, clock: FakeClock
) -> StudentDetailOut:
    return create_student(db, data, actor=actor, meta=_META, clock=clock)


def _stored(db: Session, student_id: UUID) -> Student:
    return db.execute(select(Student).where(Student.id == student_id)).scalar_one()


def _sensitive_audits(db: Session, student_id: UUID) -> list[AuditLog]:
    return list(
        db.execute(
            select(AuditLog).where(
                AuditLog.action == "student.sensitive_update",
                AuditLog.entity_id == str(student_id),
            )
        ).scalars()
    )


def test_create_student_success(db_session: Session, fake_clock: FakeClock) -> None:
    actor = _actor("students:write", "students:sensitive")
    school = make_school(db_session)
    klass = make_class(db_session, name="A班")

    out = _create(
        db_session,
        StudentCreateIn(
            student_no="S115010",
            name="林小安",
            grade_level=2,
            school_id=school.id,
            school_class="   ",
            class_id=klass.id,
            id_number=" a123456789 ",
            health_note="氣喘",
        ),
        actor,
        fake_clock,
    )

    assert isinstance(out, StudentDetailOut)
    assert (out.student_no, out.name, out.grade_level, out.status) == (
        "S115010",
        "林小安",
        2,
        "active",
    )
    assert out.school is not None
    assert out.school.id == school.id
    assert out.class_ is not None
    assert out.class_.name == "A班"
    assert out.school_class is None
    assert (out.has_id_number, out.has_health_note) == (True, True)
    assert out.sensitive is not None
    assert out.sensitive.model_dump() == {"id_number": _ID_NUMBER, "health_note": "氣喘"}
    assert out.photo_url is None
    assert out.guardians == []
    stored = _stored(db_session, out.id)
    assert stored.id_number_hmac == id_number_hmac(_ID_NUMBER)
    assert stored.id_number_enc is not None
    assert _ID_NUMBER.encode() not in stored.id_number_enc
    assert decrypt_bytes(stored.id_number_enc) == _ID_NUMBER
    assert stored.health_note_enc is not None
    assert decrypt_bytes(stored.health_note_enc) == "氣喘"
    assert stored.school_class is None
    audits = _sensitive_audits(db_session, out.id)
    assert len(audits) == 1
    assert audits[0].after == {"set": ["health_note", "id_number"]}
    assert audits[0].before is None
    assert (audits[0].actor_type, audits[0].actor_id) == ("staff", actor.id)
    assert (audits[0].entity_type, audits[0].ip) == ("student", "127.0.0.1")


def test_create_student_no_taken(db_session: Session, fake_clock: FakeClock) -> None:
    make_student(db_session, student_no="S115001")
    before = db_session.execute(select(func.count()).select_from(Student)).scalar_one()

    with pytest.raises(AppError) as exc:
        _create(
            db_session,
            StudentCreateIn(student_no="S115001", name="林小安", grade_level=2),
            _actor("students:write"),
            fake_clock,
        )

    assert (exc.value.status, exc.value.code) == (409, "student_no_taken")
    # savepoint 已 rollback：筆數不變、session 仍可用
    assert db_session.execute(select(func.count()).select_from(Student)).scalar_one() == before
    _create(
        db_session,
        StudentCreateIn(student_no="S115002", name="林小安", grade_level=2),
        _actor("students:write"),
        fake_clock,
    )


def test_create_student_id_duplicate_including_archived(
    db_session: Session, fake_clock: FakeClock
) -> None:
    archived = make_student(db_session, name="王小明", student_no="OLD001", archived=True)
    archived.id_number_enc = encrypt_bytes(_ID_NUMBER)
    archived.id_number_hmac = id_number_hmac(_ID_NUMBER)
    db_session.flush()
    actor = _actor("students:write", "students:sensitive")

    with pytest.raises(AppError) as exc:
        _create(
            db_session,
            StudentCreateIn(
                student_no="S115010", name="林小安", grade_level=2, id_number="a123456789"
            ),
            actor,
            fake_clock,
        )

    assert (exc.value.status, exc.value.code) == (409, "id_number_duplicate")
    assert exc.value.details == {
        "student_id": archived.id,
        "student_no": "OLD001",
        "name": "王小明",
    }
    assert (
        db_session.execute(
            select(func.count()).select_from(Student).where(Student.student_no == "S115010")
        ).scalar_one()
        == 0
    )
    assert _sensitive_audits(db_session, archived.id) == []


def test_create_student_sensitive_forbidden(db_session: Session, fake_clock: FakeClock) -> None:
    actor = _actor("students:write")

    with pytest.raises(AppError) as id_exc:
        _create(
            db_session,
            StudentCreateIn(
                student_no="S115010", name="林小安", grade_level=2, id_number=_ID_NUMBER
            ),
            actor,
            fake_clock,
        )
    with pytest.raises(AppError) as note_exc:
        _create(
            db_session,
            StudentCreateIn(student_no="S115010", name="林小安", grade_level=2, health_note="氣喘"),
            actor,
            fake_clock,
        )

    for exc in (id_exc, note_exc):
        assert (exc.value.status, exc.value.code) == (403, "sensitive_permission_required")
    # 不給敏感欄位（只填空白視為未給）→ 可建立，且不寫稽核
    out = _create(
        db_session,
        StudentCreateIn(
            student_no="S115010", name="林小安", grade_level=2, id_number="  ", health_note=" "
        ),
        actor,
        fake_clock,
    )
    assert (out.has_id_number, out.has_health_note, out.sensitive) == (False, False, None)
    stored = _stored(db_session, out.id)
    assert (stored.id_number_enc, stored.id_number_hmac, stored.health_note_enc) == (
        None,
        None,
        None,
    )
    assert _sensitive_audits(db_session, out.id) == []


def test_create_student_invalid_refs(db_session: Session, fake_clock: FakeClock) -> None:
    actor = _actor("students:write", "students:sensitive")
    inactive_school = make_school(db_session)
    inactive_school.is_active = False
    archived_class = make_class(db_session, archived=True)
    db_session.flush()

    def _attempt(**fields: object) -> AppError:
        with pytest.raises(AppError) as exc:
            _create(
                db_session,
                StudentCreateIn.model_validate(
                    {"student_no": "S115010", "name": "林小安", "grade_level": 2, **fields}
                ),
                actor,
                fake_clock,
            )
        return exc.value

    missing_school = _attempt(school_id=str(uuid4()))
    disabled_school = _attempt(school_id=str(inactive_school.id))
    missing_class = _attempt(class_id=str(uuid4()))
    closed_class = _attempt(class_id=str(archived_class.id))
    bad_id = _attempt(id_number="A123")
    bad_checksum = _attempt(id_number="A123456780")
    bad_dates = _attempt(status="withdrawn", enrolled_on="2026-09-01", withdrawn_on="2026-08-31")

    assert (missing_school.status, missing_school.code) == (422, "invalid_school")
    assert (disabled_school.status, disabled_school.code) == (422, "invalid_school")
    assert (missing_class.status, missing_class.code) == (422, "invalid_class")
    assert (closed_class.status, closed_class.code) == (422, "invalid_class")
    assert (bad_id.status, bad_id.code) == (422, "invalid_id_number")
    assert (bad_checksum.status, bad_checksum.code) == (422, "invalid_id_number")
    assert (bad_dates.status, bad_dates.code) == (422, "invalid_dates")
    assert (
        db_session.execute(
            select(func.count()).select_from(Student).where(Student.student_no == "S115010")
        ).scalar_one()
        == 0
    )


@pytest.mark.clock("2026-09-02T00:30:00+08:00")
def test_create_student_withdrawn_default_date(db_session: Session, fake_clock: FakeClock) -> None:
    actor = _actor("students:write")

    defaulted = _create(
        db_session,
        StudentCreateIn(student_no="S115010", name="林小安", grade_level=2, status="withdrawn"),
        actor,
        fake_clock,
    )
    explicit = _create(
        db_session,
        StudentCreateIn(
            student_no="S115011",
            name="陳小華",
            grade_level=2,
            status="withdrawn",
            enrolled_on=date(2026, 8, 1),
            withdrawn_on=date(2026, 8, 20),
        ),
        actor,
        fake_clock,
    )
    active = _create(
        db_session,
        StudentCreateIn(student_no="S115012", name="黃小美", grade_level=2),
        actor,
        fake_clock,
    )

    # UTC 仍是 9/1 16:30，台北已是 9/2
    assert fake_clock.now().date() == date(2026, 9, 1)
    assert defaulted.withdrawn_on == date(2026, 9, 2)
    assert defaulted.status == "withdrawn"
    assert explicit.withdrawn_on == date(2026, 8, 20)
    assert active.withdrawn_on is None


@pytest.fixture
def owner_cleanup_rows() -> Iterator[list[tuple[str, str, object]]]:
    """committing 測試建立的列以 owner 連線依登記反序刪除 ``(table, column, value)``；
    排在 committing_db_session 之前。學生要登記在班級之前（刪班級時學生 class_id 只會 SET NULL）。
    """
    rows: list[tuple[str, str, object]] = []
    yield rows
    with connect_owner() as conn:
        conn.execute("set lock_timeout = '5s'")
        for table, column, value in reversed(rows):
            conn.execute(
                f"delete from public.{table} where {column} = %s",  # noqa: S608  表名欄名為測試常數
                (value,),
            )
        conn.commit()


@pytest.mark.cleanup_tables("class_staff")
def test_create_student_blocked_by_concurrent_class_archive(
    owner_cleanup_rows: list[tuple[str, str, object]],
    committing_db_session: Session,
    db_engine: Engine,
    fake_clock: FakeClock,
) -> None:
    """A 封存班級未 commit（FOR UPDATE）→ B create_student(class_id) 對班級列取 FOR SHARE 必須等待
    （0.5 秒內沒結束）→ A commit → B 重讀到 archived_at → 422 invalid_class，學生不會進封存班。
    （沒有 FOR SHARE 時 B 會讀到舊的未封存狀態，INSERT 等到 A commit 後成功——這正是要擋的情境。）"""
    klass = make_class(committing_db_session)
    committing_db_session.commit()
    student_no = f"CC{uuid4().hex[:8]}"
    # 實作有誤時 B 會真的把學生 commit 進去：連學生一起清，殘留列才不會污染其他測試
    owner_cleanup_rows.append(("classes", "id", klass.id))
    owner_cleanup_rows.append(("students", "student_no", student_no))
    data = StudentCreateIn(student_no=student_no, name="林小安", grade_level=2, class_id=klass.id)
    a_locked = threading.Event()
    release_a = threading.Event()
    b_done = threading.Event()
    outcome: dict[str, object] = {}

    def worker_a() -> None:
        sa = Session(bind=db_engine)
        try:
            archive_class(sa, klass.id, clock=fake_clock)
            a_locked.set()
            release_a.wait(timeout=10)
            sa.commit()
        except BaseException as exc:
            outcome["a_error"] = exc
            a_locked.set()
        finally:
            sa.close()

    def worker_b() -> None:
        sb = Session(bind=db_engine)
        try:
            a_locked.wait(timeout=10)
            try:
                create_student(
                    sb, data, actor=_actor("students:write"), meta=_META, clock=fake_clock
                )
                sb.commit()
                outcome["b"] = "created"
            except AppError as exc:
                sb.rollback()
                outcome["b"] = (exc.status, exc.code)
        except BaseException as exc:
            outcome["b_error"] = exc
        finally:
            sb.close()
            b_done.set()

    threads = [threading.Thread(target=worker_a), threading.Thread(target=worker_b)]
    for t in threads:
        t.start()
    try:
        assert a_locked.wait(timeout=10)
        assert not b_done.wait(timeout=0.5), outcome  # A 尚未 commit：B 的 FOR SHARE 被擋住
    finally:
        release_a.set()
        for t in threads:
            t.join(timeout=10)

    assert "a_error" not in outcome, outcome
    assert "b_error" not in outcome, outcome
    assert outcome["b"] == (422, "invalid_class")
    with Session(bind=db_engine) as check:
        count = check.execute(
            select(func.count()).select_from(Student).where(Student.class_id == klass.id)
        ).scalar_one()
    assert count == 0


# --- BACKEND-153：archive_student ----------------------------------------------------------------


def _add_binding_code(
    db: Session, guardian_id: UUID, clock: FakeClock, *, code: str, used: bool
) -> ParentBindingCode:
    row = ParentBindingCode(
        created_at=clock.now() - timedelta(days=1),
        guardian_id=guardian_id,
        code_hash=hash_code(code),
        expires_at=clock.now() + timedelta(days=6),
        used_at=clock.now() - timedelta(hours=1) if used else None,
        created_by=make_staff(db).id,
    )
    db.add(row)
    db.flush()
    return row


def _code_count(db: Session, guardian_id: UUID, *, used: bool) -> int:
    stmt = (
        select(func.count())
        .select_from(ParentBindingCode)
        .where(ParentBindingCode.guardian_id == guardian_id)
    )
    stmt = stmt.where(
        ParentBindingCode.used_at.is_not(None) if used else ParentBindingCode.used_at.is_(None)
    )
    return db.execute(stmt).scalar_one()


def test_archive_student_success(db_session: Session, fake_clock: FakeClock) -> None:
    student = make_student(db_session, name="王小明")
    mother = make_guardian(db_session, student, name="王媽媽")
    father = make_guardian(db_session, student, name="王爸爸", relation="father")
    _add_binding_code(db_session, mother.id, fake_clock, code="MOTHER01", used=False)
    _add_binding_code(db_session, mother.id, fake_clock, code="MOTHER02", used=True)
    _add_binding_code(db_session, father.id, fake_clock, code="FATHER01", used=False)
    other = make_guardian(db_session, make_student(db_session, name="陳小華"), name="陳媽媽")
    _add_binding_code(db_session, other.id, fake_clock, code="OTHER001", used=False)

    out = archive_student(db_session, student.id, clock=fake_clock)

    assert isinstance(out, StudentDetailOut)
    assert out.id == student.id
    assert out.archived_at == fake_clock.now()
    assert _stored(db_session, student.id).archived_at == fake_clock.now()
    assert _code_count(db_session, mother.id, used=False) == 0
    assert _code_count(db_session, mother.id, used=True) == 1
    assert _code_count(db_session, father.id, used=False) == 0
    # 其他學生的綁定碼不受影響
    assert _code_count(db_session, other.id, used=False) == 1


def test_archive_student_parent_scope(db_session: Session, fake_clock: FakeClock) -> None:
    student = make_student(db_session, name="王小明")
    sibling = make_student(db_session, name="王小華")
    parent = make_parent(db_session)
    make_guardian(db_session, student, parent=parent)
    make_guardian(db_session, sibling, parent=parent)
    assert student.id in get_parent_student_ids(db_session, parent.id)

    archive_student(db_session, student.id, clock=fake_clock)

    assert get_parent_student_ids(db_session, parent.id) == [sibling.id]


def test_archive_student_idempotent(db_session: Session, fake_clock: FakeClock) -> None:
    student = make_student(db_session)
    first = archive_student(db_session, student.id, clock=fake_clock)
    fake_clock.advance(hours=2)

    again = archive_student(db_session, student.id, clock=fake_clock)

    assert again.archived_at == first.archived_at
    assert again.archived_at != fake_clock.now()
    with pytest.raises(AppError) as exc:
        archive_student(db_session, uuid4(), clock=fake_clock)
    assert (exc.value.status, exc.value.code) == (404, "student_not_found")


# --- BACKEND-154：upload_photo -------------------------------------------------------------------


def _upload(
    content: bytes, *, filename: str = "photo.jpg", content_type: str = "image/jpeg"
) -> UploadFile:
    return UploadFile(
        file=io.BytesIO(content), filename=filename, headers=Headers({"content-type": content_type})
    )


def _seed_old_photo(db: Session, student: Student, storage: FakeStorage) -> str:
    old = build_object_path(student.id, "jpg")
    student.photo_path = old
    db.flush()
    storage.objects[("student-photos", old)] = b"old"
    return old


def test_upload_photo_success(db_session: Session) -> None:
    student = make_student(db_session)
    storage = FakeStorage()

    out = upload_photo(db_session, student.id, _upload(_JPEG), storage=storage)

    assert out.photo_url.startswith(_PHOTO_URL_PREFIX)
    stored = _stored(db_session, student.id)
    assert stored.photo_path is not None
    assert re.fullmatch(rf"{student.id}/[0-9a-f]{{32}}\.jpg", stored.photo_path)
    assert out.photo_url == f"{_PHOTO_URL_PREFIX}{stored.photo_path}?exp=300"
    assert storage.objects[("student-photos", stored.photo_path)] == _JPEG
    assert storage.content_types[("student-photos", stored.photo_path)] == "image/jpeg"
    # 檔頭判定格式，不信任檔名與 content-type
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 50
    out_png = upload_photo(db_session, student.id, _upload(png, filename="x.jpg"), storage=storage)
    assert out_png.photo_url.endswith(".png?exp=300")


def test_upload_photo_old_deleted_after_commit(db_session: Session) -> None:
    install_tx_hooks()
    storage = FakeStorage()
    committed = make_student(db_session)
    old_committed = _seed_old_photo(db_session, committed, storage)

    upload_photo(db_session, committed.id, _upload(_JPEG), storage=storage)
    new_path = _stored(db_session, committed.id).photo_path
    assert new_path is not None
    # commit 前舊檔仍在
    assert ("student-photos", old_committed) in storage.objects
    db_session.commit()
    assert ("student-photos", old_committed) not in storage.objects
    assert ("student-photos", new_path) in storage.objects

    rolled_back = make_student(db_session)
    old_rolled_back = _seed_old_photo(db_session, rolled_back, storage)
    db_session.commit()
    upload_photo(db_session, rolled_back.id, _upload(_JPEG), storage=storage)
    db_session.rollback()
    assert ("student-photos", old_rolled_back) in storage.objects
    assert _stored(db_session, rolled_back.id).photo_path == old_rolled_back


def test_upload_photo_invalid_type(db_session: Session) -> None:
    student = make_student(db_session)
    storage = FakeStorage()

    with pytest.raises(AppError) as pdf:
        upload_photo(db_session, student.id, _upload(_PDF, filename="a.pdf"), storage=storage)
    with pytest.raises(AppError) as huge:
        upload_photo(
            db_session,
            student.id,
            _upload(b"\xff\xd8\xff\xe0" + b"0" * (PHOTO_MAX_BYTES + 1)),
            storage=storage,
        )

    assert (pdf.value.status, pdf.value.code) == (415, "unsupported_file_type")
    assert (huge.value.status, huge.value.code) == (413, "file_too_large")
    assert storage.objects == {}
    assert _stored(db_session, student.id).photo_path is None


def test_upload_photo_storage_error(db_session: Session) -> None:
    student = make_student(db_session)
    storage = FakeStorage()
    old = _seed_old_photo(db_session, student, storage)
    storage.upload_error = StorageError("上傳失敗")

    with pytest.raises(AppError) as exc:
        upload_photo(db_session, student.id, _upload(_JPEG), storage=storage)

    assert (exc.value.status, exc.value.code) == (502, "storage_unavailable")
    assert _stored(db_session, student.id).photo_path == old
    assert list(storage.objects) == [("student-photos", old)]


def test_upload_photo_archived(db_session: Session) -> None:
    archived = make_student(db_session, archived=True)
    storage = FakeStorage()

    with pytest.raises(AppError) as exc:
        upload_photo(db_session, archived.id, _upload(_JPEG), storage=storage)
    with pytest.raises(AppError) as missing:
        upload_photo(db_session, uuid4(), _upload(_JPEG), storage=storage)

    assert (exc.value.status, exc.value.code) == (404, "student_not_found")
    assert (missing.value.status, missing.value.code) == (404, "student_not_found")
    assert storage.objects == {}


@pytest.mark.cleanup_tables("class_staff")
def test_upload_photo_concurrent_uploads_leave_no_orphan(
    owner_cleanup_rows: list[tuple[str, str, object]],
    committing_db_session: Session,
    db_engine: Engine,
) -> None:
    """A 上傳並 flush 未 commit → B 上傳必須等 A 的學生列鎖（0.5 秒內未結束）→ A commit → B 重讀到
    A 的 path 當舊檔 → B commit 後 Storage 只剩 B 的物件（沒鎖時 B 讀到更舊的 path，A 的照片變
    孤兒）。"""
    install_tx_hooks()
    storage = FakeStorage()
    student = make_student(committing_db_session)
    original = _seed_old_photo(committing_db_session, student, storage)
    committing_db_session.commit()
    owner_cleanup_rows.append(("students", "id", student.id))
    student_id = student.id
    a_locked = threading.Event()
    release_a = threading.Event()
    b_done = threading.Event()
    outcome: dict[str, object] = {}

    def worker_a() -> None:
        sa = Session(bind=db_engine)
        try:
            upload_photo(sa, student_id, _upload(_JPEG), storage=storage)
            a_locked.set()
            release_a.wait(timeout=10)
            sa.commit()
        except BaseException as exc:
            outcome["a_error"] = exc
            a_locked.set()
        finally:
            sa.close()

    def worker_b() -> None:
        sb = Session(bind=db_engine)
        try:
            a_locked.wait(timeout=10)
            upload_photo(sb, student_id, _upload(_JPEG), storage=storage)
            sb.commit()
        except BaseException as exc:
            outcome["b_error"] = exc
        finally:
            sb.close()
            b_done.set()

    threads = [threading.Thread(target=worker_a), threading.Thread(target=worker_b)]
    for t in threads:
        t.start()
    try:
        assert a_locked.wait(timeout=10)
        assert not b_done.wait(timeout=0.5), outcome  # A 尚未 commit：B 被學生列鎖擋住
    finally:
        release_a.set()
        for t in threads:
            t.join(timeout=10)

    assert "a_error" not in outcome, outcome
    assert "b_error" not in outcome, outcome
    with Session(bind=db_engine) as check:
        final_path = _stored(check, student_id).photo_path
    assert final_path is not None
    assert final_path != original
    # 只剩最後一次上傳的物件：原檔與 A 的物件都已在各自 commit 後刪除
    assert list(storage.objects) == [("student-photos", final_path)]


# --- BACKEND-530：purge_student ------------------------------------------------------------------


class _Purgeable:
    """封存且 withdrawn 的王小明（S115001）與他的全部關聯資料。"""

    def __init__(self, db: Session, clock: FakeClock, storage: FakeStorage) -> None:
        self.klass = make_class(db)
        # withdrawn 需同時有 withdrawn_on（DB CHECK），先建 active 再改狀態
        self.student = make_student(db, name="王小明", student_no="S115001", class_=self.klass)
        self.student.status = "withdrawn"
        self.student.withdrawn_on = date(2026, 8, 31)
        self.student.enrolled_on = date(2025, 9, 1)
        self.student.birthday = date(2017, 5, 1)
        self.student.gender = "male"
        self.student.school_class = "三年二班"
        self.student.note = "王小明家長備註"
        self.student.id_number_enc = encrypt_bytes(_ID_NUMBER)
        self.student.id_number_hmac = id_number_hmac(_ID_NUMBER)
        self.student.health_note_enc = encrypt_bytes("對花生過敏")
        self.student.archived_at = clock.now() - timedelta(days=1)
        self.photo = build_object_path(self.student.id, "jpg")
        self.student.photo_path = self.photo
        storage.objects[("student-photos", self.photo)] = b"photo"

        self.parent = make_parent(db, display_name="王媽媽")
        self.guardian = make_guardian(
            db, self.student, parent=self.parent, name="王媽媽", is_primary=True
        )
        self.guardian.phone = "0912-000-123"
        _add_binding_code(db, self.guardian.id, clock, code="WANGMOM1", used=False)

        self.person_photo = f"{uuid4()}/{uuid4().hex}.jpg"
        self.person = make_pickup_person(
            db, self.student, name="李阿姨", phone="0912-000-101", photo_path=self.person_photo
        )
        storage.objects[("pickup-person-photos", self.person_photo)] = b"person"
        self.authorization = make_pickup_authorization(
            db,
            self.student,
            service_date=clock.today(),
            person=self.person,
            proxy_name="李阿姨",
            proxy_phone="0912-000-101",
        )

        for offset in range(5):
            make_attendance(
                db,
                self.student,
                service_date=date(2026, 8, 25) + timedelta(days=offset),
                status="present",
                note="王小明發燒" if offset == 0 else None,
            )
        exam = make_exam(db, class_=self.klass)
        subjects = db.execute(select(Subject).order_by(Subject.sort_order).limit(3)).scalars().all()
        for subject in subjects:
            make_exam_subject(db, exam, subject)
            score = make_exam_score(db, exam, self.student, subject)
            score.note = "王小明請假補考"
        for offset in (0, 1):
            make_pickup_request(
                db, self.student, service_date=date(2026, 8, 25) + timedelta(days=offset)
            )

        self.leave = make_leave(
            db, self.student, start_date=date(2026, 8, 27), reason="王小明發燒就醫"
        )
        self.attachment = make_leave_attachment(db, self.leave, ext="pdf")
        storage.objects[("leave-attachments", self.attachment.storage_path)] = b"pdf"

        for title in ("王小明已到班", "王小明作業完成"):
            db.add(
                Notification(
                    recipient_type="parent",
                    recipient_id=self.parent.id,
                    event="attendance.checked_in",
                    title=title,
                    body=f"{title}（通知內文）",
                    payload={"student_id": str(self.student.id), "name": "王小明"},
                )
            )
        # 另一位學生的通知不受影響
        self.other = make_student(db, name="陳小華")
        db.add(
            Notification(
                recipient_type="parent",
                recipient_id=self.parent.id,
                event="attendance.checked_in",
                title="陳小華已到班",
                body="陳小華已到班",
                payload={"student_id": str(self.other.id)},
            )
        )
        db.flush()


def _purge(
    db: Session,
    student_id: UUID,
    actor: CurrentStaff,
    clock: FakeClock,
    storage: FakeStorage,
    *,
    confirm: str = "S115001",
) -> StudentPurgeOut:
    return purge_student(
        db,
        student_id,
        StudentPurgeIn(confirm_student_no=confirm),
        actor=actor,
        storage=storage,
        meta=_META,
        clock=clock,
    )


def _purger() -> CurrentStaff:
    return _actor("students:write", "students:purge")


def _count(db: Session, model: type, student_id: UUID) -> int:
    return db.execute(
        select(func.count()).select_from(model).where(model.student_id == student_id)  # type: ignore[attr-defined]
    ).scalar_one()


def _notifications_for(db: Session, student_id: UUID) -> int:
    return db.execute(
        select(func.count())
        .select_from(Notification)
        .where(Notification.payload["student_id"].astext == str(student_id))
    ).scalar_one()


def test_purge_student_preconditions(db_session: Session, fake_clock: FakeClock) -> None:
    storage = FakeStorage()
    active = make_student(db_session, student_no="ACT001", archived=True)
    withdrawn_not_archived = make_student(db_session, student_no="WD001")
    withdrawn_not_archived.status = "withdrawn"
    withdrawn_not_archived.withdrawn_on = date(2026, 8, 31)
    db_session.flush()
    target = _Purgeable(db_session, fake_clock, storage)

    with pytest.raises(AppError) as still_active:
        _purge(db_session, active.id, _purger(), fake_clock, storage, confirm="ACT001")
    with pytest.raises(AppError) as not_archived:
        _purge(
            db_session, withdrawn_not_archived.id, _purger(), fake_clock, storage, confirm="WD001"
        )
    with pytest.raises(AppError) as mismatch:
        _purge(db_session, target.student.id, _purger(), fake_clock, storage, confirm="WRONG")
    with pytest.raises(AppError) as forbidden:
        _purge(db_session, target.student.id, _actor("students:write"), fake_clock, storage)
    with pytest.raises(AppError) as missing:
        _purge(db_session, uuid4(), _purger(), fake_clock, storage)

    assert (still_active.value.status, still_active.value.code) == (409, "student_not_purgeable")
    assert (not_archived.value.status, not_archived.value.code) == (409, "student_not_purgeable")
    assert (mismatch.value.status, mismatch.value.code) == (422, "purge_confirmation_mismatch")
    assert (forbidden.value.status, forbidden.value.code) == (403, "permission_denied")
    assert (missing.value.status, missing.value.code) == (404, "student_not_found")
    # 全部失敗：資料完全沒動
    assert _stored(db_session, target.student.id).name == "王小明"
    assert _stored(db_session, active.id).name == "王小明"
    assert len(storage.objects) == 3


def test_purge_student_anonymizes(db_session: Session, fake_clock: FakeClock) -> None:
    storage = FakeStorage()
    target = _Purgeable(db_session, fake_clock, storage)

    out = _purge(db_session, target.student.id, _purger(), fake_clock, storage)

    assert isinstance(out, StudentPurgeOut)
    assert out.student_id == target.student.id
    assert out.purged_at == fake_clock.now()
    assert re.fullmatch(r"DEL-[0-9a-f]{8}", out.anonymized_student_no)
    db_session.expire_all()
    stored = _stored(db_session, target.student.id)
    assert stored.name == "已刪除學生"
    assert stored.student_no == out.anonymized_student_no
    assert (
        stored.id_number_enc,
        stored.id_number_hmac,
        stored.health_note_enc,
        stored.photo_path,
        stored.birthday,
        stored.note,
        stored.gender,
        stored.school_class,
    ) == (None,) * 8
    assert (stored.grade_level, stored.class_id, stored.status) == (3, target.klass.id, "withdrawn")
    assert (stored.enrolled_on, stored.withdrawn_on) == (date(2025, 9, 1), date(2026, 8, 31))
    assert stored.archived_at is not None
    # 其他學生不受影響
    assert _stored(db_session, target.other.id).name == "陳小華"


def test_purge_student_guardians_and_pickup(db_session: Session, fake_clock: FakeClock) -> None:
    storage = FakeStorage()
    target = _Purgeable(db_session, fake_clock, storage)
    assert _code_count(db_session, target.guardian.id, used=False) == 1

    _purge(db_session, target.student.id, _purger(), fake_clock, storage)

    db_session.expire_all()
    guardian = db_session.get(Guardian, target.guardian.id)
    assert guardian is not None
    assert (guardian.name, guardian.phone, guardian.parent_account_id) == ("已刪除", None, None)
    assert guardian.is_primary is False
    assert guardian.archived_at == fake_clock.now()
    assert _code_count(db_session, target.guardian.id, used=False) == 0
    person = db_session.get(PickupPerson, target.person.id)
    assert person is not None
    assert (person.name, person.phone, person.photo_path) == ("已刪除", "00000000", None)
    assert person.archived_at == fake_clock.now()
    authorization = db_session.get(PickupAuthorization, target.authorization.id)
    assert authorization is not None
    assert (authorization.proxy_name, authorization.proxy_phone) == ("已刪除", "00000000")
    # 家長帳號本身保留（可能綁定其他小孩）
    parent = db_session.get(ParentAccount, target.parent.id)
    assert parent is not None
    assert parent.display_name == "王媽媽"
    assert get_parent_student_ids(db_session, target.parent.id) == []


def test_purge_student_keeps_statistics(db_session: Session, fake_clock: FakeClock) -> None:
    storage = FakeStorage()
    target = _Purgeable(db_session, fake_clock, storage)
    sid = target.student.id
    assert (_count(db_session, StudentAttendance, sid), _count(db_session, ExamScore, sid)) == (
        5,
        3,
    )
    assert _count(db_session, PickupRequest, sid) == 2
    assert _notifications_for(db_session, sid) == 2

    _purge(db_session, sid, _purger(), fake_clock, storage)

    db_session.expire_all()
    assert _count(db_session, StudentAttendance, sid) == 5
    assert _count(db_session, ExamScore, sid) == 3
    assert _count(db_session, PickupRequest, sid) == 2
    assert _count(db_session, StudentLeave, sid) == 1
    notes = db_session.execute(
        select(StudentAttendance.note).where(StudentAttendance.student_id == sid)
    ).scalars()
    assert list(notes) == [None] * 5
    score_notes = db_session.execute(select(ExamScore.note).where(ExamScore.student_id == sid))
    assert list(score_notes.scalars()) == [None] * 3
    leave = db_session.get(StudentLeave, target.leave.id)
    assert leave is not None
    assert leave.reason is None
    assert (
        db_session.execute(
            select(func.count())
            .select_from(StudentLeaveAttachment)
            .where(StudentLeaveAttachment.leave_id == target.leave.id)
        ).scalar_one()
        == 0
    )
    assert _notifications_for(db_session, sid) == 0
    assert _notifications_for(db_session, target.other.id) == 1


def test_purge_student_storage_after_commit(db_session: Session, fake_clock: FakeClock) -> None:
    install_tx_hooks()
    storage = FakeStorage()
    committed = _Purgeable(db_session, fake_clock, storage)
    committed_paths = {
        ("student-photos", committed.photo),
        ("leave-attachments", committed.attachment.storage_path),
        ("pickup-person-photos", committed.person_photo),
    }
    db_session.commit()

    _purge(db_session, committed.student.id, _purger(), fake_clock, storage)
    assert committed_paths <= set(storage.objects)  # commit 前不刪
    db_session.commit()
    assert committed_paths.isdisjoint(storage.objects)

    rolled_back = _Purgeable(db_session, fake_clock, storage)
    rolled_back_paths = {
        ("student-photos", rolled_back.photo),
        ("leave-attachments", rolled_back.attachment.storage_path),
        ("pickup-person-photos", rolled_back.person_photo),
    }
    db_session.commit()
    _purge(db_session, rolled_back.student.id, _purger(), fake_clock, storage)
    db_session.rollback()
    assert rolled_back_paths <= set(storage.objects)
    assert _stored(db_session, rolled_back.student.id).name == "王小明"


def test_purge_student_audit_and_idempotent(db_session: Session, fake_clock: FakeClock) -> None:
    storage = FakeStorage()
    target = _Purgeable(db_session, fake_clock, storage)
    actor = _purger()

    _purge(db_session, target.student.id, actor, fake_clock, storage)

    audits = list(
        db_session.execute(
            select(AuditLog).where(
                AuditLog.action == "student.purge", AuditLog.entity_id == str(target.student.id)
            )
        ).scalars()
    )
    assert len(audits) == 1
    audit = audits[0]
    assert (audit.actor_type, audit.actor_id, audit.entity_type) == ("staff", actor.id, "student")
    assert audit.after == {
        "guardians": 1,
        "pickup_persons": 1,
        "attachments": 1,
        "notifications": 2,
    }
    dumped = json.dumps(audit.after, ensure_ascii=False) + json.dumps(audit.before)
    for leaked in ("王小明", "S115001", "0912", _ID_NUMBER, "王媽媽", "李阿姨"):
        assert leaked not in dumped
    with pytest.raises(AppError) as exc:
        _purge(
            db_session,
            target.student.id,
            actor,
            fake_clock,
            storage,
            confirm=_stored(db_session, target.student.id).student_no,
        )
    assert (exc.value.status, exc.value.code) == (409, "student_already_purged")


# --- BACKEND-529：close_out_inactive_student ------------------------------------------------------

_D = date(2026, 9, 9)  # @pytest.mark.clock 設定的台北今天


def _withdrawn_student(db: Session, name: str = "王小明") -> Student:
    student = make_student(db, name=name)
    student.status = "withdrawn"
    student.withdrawn_on = _D
    db.flush()
    return student


def _close_out(db: Session, student: Student, clock: FakeClock) -> CloseOutResult:
    return close_out_inactive_student(db, student, actor=_actor("students:write"), clock=clock)


@pytest.mark.clock("2026-09-09T10:00:00+08:00")
def test_close_out_pickup_requests(db_session: Session, fake_clock: FakeClock) -> None:
    ming = _withdrawn_student(db_session)
    pending = make_pickup_request(db_session, ming, service_date=_D)
    done = make_pickup_request(
        db_session, ming, service_date=_D - timedelta(days=1), status="completed"
    )
    other = make_pickup_request(
        db_session, make_student(db_session, name="陳小華"), service_date=_D
    )

    result = _close_out(db_session, ming, fake_clock)

    db_session.expire_all()
    assert pending.status == "cancelled"
    assert pending.cancel_reason == "學生已退班"
    assert pending.cancelled_at == fake_clock.now()
    assert done.status == "completed"
    assert other.status == "pending"
    assert result.cancelled_pickup_requests == 1
    # 停讀文案
    suspended = make_student(db_session, name="停讀生")
    suspended.status = "suspended"
    req = make_pickup_request(db_session, suspended, service_date=_D, status="acknowledged")
    _close_out(db_session, suspended, fake_clock)
    db_session.expire_all()
    assert (req.status, req.cancel_reason) == ("cancelled", "學生已停讀")


@pytest.mark.clock("2026-09-09T10:00:00+08:00")
def test_close_out_authorizations(db_session: Session, fake_clock: FakeClock) -> None:
    ming = _withdrawn_student(db_session)
    today = make_pickup_authorization(db_session, ming, service_date=_D, code="111111")
    tomorrow = make_pickup_authorization(
        db_session, ming, service_date=_D + timedelta(days=1), code="222222"
    )
    yesterday = make_pickup_authorization(
        db_session, ming, service_date=_D - timedelta(days=1), code="333333"
    )
    completed = make_pickup_authorization(
        db_session, ming, service_date=_D, code="444444", status="completed"
    )

    result = _close_out(db_session, ming, fake_clock)

    db_session.expire_all()
    assert (today.status, tomorrow.status) == ("cancelled", "cancelled")
    assert yesterday.status == "active"
    assert completed.status == "completed"
    assert result.cancelled_authorizations == 2


@pytest.mark.clock("2026-09-09T10:00:00+08:00")
def test_close_out_attendance(db_session: Session, fake_clock: FakeClock) -> None:
    ming = _withdrawn_student(db_session)
    other = make_student(db_session, name="陳小華")
    present = make_attendance(
        db_session, ming, service_date=_D - timedelta(days=1), status="present"
    )
    make_attendance(db_session, ming, service_date=_D)
    make_attendance(db_session, ming, service_date=_D + timedelta(days=1))
    others_row = make_attendance(db_session, other, service_date=_D)

    result = _close_out(db_session, ming, fake_clock)

    assert result.deleted_attendance_dates == [_D, _D + timedelta(days=1)]
    rows = db_session.execute(
        select(StudentAttendance.service_date, StudentAttendance.status).where(
            StudentAttendance.student_id == ming.id
        )
    ).all()
    assert rows == [(_D - timedelta(days=1), "present")]
    db_session.expire_all()
    assert present.status == "present"
    assert others_row.status == "expected"


@pytest.mark.clock("2026-09-09T10:00:00+08:00")
def test_close_out_leaves(db_session: Session, fake_clock: FakeClock) -> None:
    ming = _withdrawn_student(db_session)
    leave_a = make_leave(
        db_session, ming, start_date=_D - timedelta(days=2), end_date=_D + timedelta(days=2)
    )
    make_attendance(db_session, ming, service_date=_D, status="leave", leave=leave_a)
    past_leave_row = make_attendance(
        db_session, ming, service_date=_D - timedelta(days=1), status="leave", leave=leave_a
    )
    leave_b = make_leave(
        db_session,
        ming,
        start_date=_D + timedelta(days=6),
        end_date=_D + timedelta(days=7),
        leave_type="personal",
    )
    ended = make_leave(db_session, ming, start_date=_D - timedelta(days=10))

    result = _close_out(db_session, ming, fake_clock)

    db_session.expire_all()
    assert (leave_a.end_date, leave_a.status) == (_D - timedelta(days=1), "active")
    assert (leave_b.status, leave_b.cancelled_by_type, leave_b.cancelled_by_id is not None) == (
        "cancelled",
        "staff",
        True,
    )
    assert leave_b.cancelled_at == fake_clock.now()
    assert ended.status == "active"
    assert (result.truncated_leaves, result.cancelled_leaves) == (1, 1)
    today_rows = (
        db_session.execute(
            select(StudentAttendance).where(
                StudentAttendance.student_id == ming.id, StudentAttendance.service_date == _D
            )
        )
        .scalars()
        .all()
    )
    assert today_rows == []  # 9/9 先還原為 expected，再被刪除
    assert result.deleted_attendance_dates == [_D]
    assert (past_leave_row.status, past_leave_row.leave_id) == ("leave", leave_a.id)  # 過去的保留
    assert (
        db_session.execute(
            select(func.count())
            .select_from(Notification)
            .where(Notification.event == "leave.cancelled")
        ).scalar_one()
        == 0
    )


@pytest.mark.clock("2026-09-09T10:00:00+08:00")
def test_close_out_keeps_history_and_audit(db_session: Session, fake_clock: FakeClock) -> None:
    ming = _withdrawn_student(db_session)
    left = make_attendance(db_session, ming, service_date=date(2026, 9, 1), status="left")
    exam = make_exam(db_session, status="published")
    subject = db_session.execute(select(Subject).order_by(Subject.sort_order).limit(1)).scalar_one()
    make_exam_subject(db_session, exam, subject)
    score = make_exam_score(db_session, exam, ming, subject)
    completed = make_pickup_request(
        db_session, ming, service_date=_D - timedelta(days=1), status="completed"
    )

    result = _close_out(db_session, ming, fake_clock)

    db_session.expire_all()
    assert left.status == "left"
    assert score.score is not None
    assert completed.status == "completed"
    assert result == CloseOutResult(
        cancelled_pickup_requests=0,
        cancelled_authorizations=0,
        cancelled_leaves=0,
        truncated_leaves=0,
        deleted_attendance_dates=[],
    )
    audits = (
        db_session.execute(
            select(AuditLog).where(
                AuditLog.action == "student.close_out", AuditLog.entity_id == str(ming.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(audits) == 1
    assert audits[0].after == {
        "status": "withdrawn",
        "cancelled_pickup_requests": 0,
        "cancelled_authorizations": 0,
        "cancelled_leaves": 0,
        "truncated_leaves": 0,
        "deleted_attendance_dates": [],
    }
    assert "王小明" not in json.dumps(audits[0].after, ensure_ascii=False)


@pytest.mark.clock("2026-09-09T10:00:00+08:00")
def test_close_out_rollback_with_caller(db_session: Session, fake_clock: FakeClock) -> None:
    ming = _withdrawn_student(db_session)
    request = make_pickup_request(db_session, ming, service_date=_D)
    auth = make_pickup_authorization(db_session, ming, service_date=_D)
    attendance = make_attendance(db_session, ming, service_date=_D)
    leave = make_leave(db_session, ming, start_date=_D + timedelta(days=3))
    db_session.commit()  # savepoint 模式：只釋放 savepoint，測試結束仍整筆 rollback
    ids = (request.id, auth.id, attendance.id, leave.id, ming.id)

    result = _close_out(db_session, ming, fake_clock)
    assert result.cancelled_pickup_requests == 1
    db_session.rollback()

    request_id, auth_id, attendance_id, leave_id, _ = ids
    assert db_session.get(PickupRequest, request_id).status == "pending"  # type: ignore[union-attr]
    assert db_session.get(PickupAuthorization, auth_id).status == "active"  # type: ignore[union-attr]
    assert db_session.get(StudentAttendance, attendance_id) is not None
    assert db_session.get(StudentLeave, leave_id).status == "active"  # type: ignore[union-attr]
    assert (
        db_session.execute(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.action == "student.close_out", AuditLog.entity_id == str(ids[4]))
        ).scalar_one()
        == 0
    )
