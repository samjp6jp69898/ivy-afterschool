"""BACKEND-417 / 418 / 419：app/services/pickup/persons.py（常用接送人列表、新增、封存）。"""

import io
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session
from starlette.datastructures import Headers, UploadFile

from app.api.deps import CurrentParent
from app.core.errors import AppError
from app.core.storage import StorageError, build_object_path
from app.models.parents import ParentAccount
from app.models.pickup import PickupPerson
from app.schemas.pickup import PickupPersonCreateIn
from app.services.pickup.persons import (
    archive_pickup_person,
    create_pickup_person,
    list_pickup_persons,
)
from app.services.settings_service import clear_settings_cache, invalidate_setting
from tests.support.factories import (
    ARCHIVED_AT,
    make_guardian,
    make_parent,
    make_pickup_person,
    make_student,
)
from tests.support.fake_clock import FakeClock
from tests.support.fake_storage import FakeStorage

_JPEG = b"\xff\xd8\xff\xe0" + b"0" * 100
_PDF = b"%PDF-1.7\n" + b"0" * 50
_URL_PREFIX = "https://storage.test/pickup-person-photos/"
_NOW = datetime(2026, 10, 5, 2, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _cache() -> Iterator[None]:
    clear_settings_cache()
    yield
    clear_settings_cache()


def _current(parent: ParentAccount) -> CurrentParent:
    return CurrentParent(
        id=parent.id,
        line_user_id=parent.line_user_id,
        display_name=parent.display_name,
        token_version=0,
    )


def _jpeg() -> UploadFile:
    return UploadFile(
        file=io.BytesIO(_JPEG), filename="a.jpg", headers=Headers({"content-type": "image/jpeg"})
    )


def _data(name: str = "李阿姨") -> PickupPersonCreateIn:
    return PickupPersonCreateIn(name=name, relation="阿姨", phone="0912-000-101")


def _set_max_per_student(session: Session, value: int) -> None:
    session.execute(
        text("update public.system_settings set value = cast(:v as jsonb) where key = :k"),
        {"v": json.dumps({"max_per_student": value}), "k": "pickup.persons"},
    )
    invalidate_setting("pickup.persons")


def _person_count(session: Session, student_id: object) -> int:
    return session.execute(
        select(func.count()).select_from(PickupPerson).where(PickupPerson.student_id == student_id)
    ).scalar_one()


def test_list_pickup_persons(db_session: Session) -> None:
    storage = FakeStorage()
    student = make_student(db_session)
    other = make_student(db_session, name="林小安")
    photo = build_object_path(uuid4(), "jpg")
    make_pickup_person(db_session, student, name="李阿姨", photo_path=photo)
    archived = make_pickup_person(db_session, student, name="張伯伯", relation="伯伯")
    archived.archived_at = ARCHIVED_AT
    make_pickup_person(db_session, other, name="別人的接送人")
    db_session.flush()

    persons = list_pickup_persons(db_session, student.id, storage=storage)

    assert [(p.name, p.relation, p.phone, p.student_id) for p in persons] == [
        ("李阿姨", "阿姨", "0912-000-101", student.id)
    ]
    assert persons[0].photo_url is not None
    assert persons[0].photo_url.startswith(_URL_PREFIX)
    assert persons[0].photo_url == f"{_URL_PREFIX}{photo}?exp=300"


def test_list_pickup_persons_without_photo_and_order(db_session: Session) -> None:
    storage = FakeStorage()
    student = make_student(db_session)
    later = make_pickup_person(db_session, student, name="後建立")
    earlier = make_pickup_person(db_session, student, name="先建立")
    later.created_at = ARCHIVED_AT + timedelta(days=1)
    earlier.created_at = ARCHIVED_AT
    db_session.flush()

    persons = list_pickup_persons(db_session, student.id, storage=storage)

    assert [(p.name, p.photo_url) for p in persons] == [("先建立", None), ("後建立", None)]


def test_list_pickup_persons_sign_error(db_session: Session) -> None:
    storage = FakeStorage()
    storage.sign_error = StorageError("S3 generate_presigned_url 失敗")
    student = make_student(db_session)
    make_pickup_person(db_session, student, photo_path=build_object_path(uuid4(), "jpg"))

    persons = list_pickup_persons(db_session, student.id, storage=storage)

    assert [(p.name, p.relation, p.phone, p.photo_url) for p in persons] == [
        ("李阿姨", "阿姨", "0912-000-101", None)
    ]


def test_create_pickup_person_with_photo(db_session: Session) -> None:
    storage = FakeStorage()
    parent = make_parent(db_session)
    student = make_student(db_session)

    out = create_pickup_person(
        db_session, student.id, _data(), _jpeg(), parent=_current(parent), storage=storage
    )

    row = db_session.execute(select(PickupPerson).where(PickupPerson.id == out.id)).scalar_one()
    assert row.photo_path is not None
    assert row.photo_path.startswith(f"{out.id}/")
    assert row.photo_path.endswith(".jpg")
    assert row.created_by_parent_id == parent.id
    assert (row.student_id, row.name, row.relation, row.phone) == (
        student.id,
        "李阿姨",
        "阿姨",
        "0912-000-101",
    )
    assert out.photo_url == f"{_URL_PREFIX}{row.photo_path}?exp=300"
    assert storage.objects == {("pickup-person-photos", row.photo_path): _JPEG}
    assert storage.content_types[("pickup-person-photos", row.photo_path)] == "image/jpeg"


def test_create_pickup_person_without_photo(db_session: Session) -> None:
    storage = FakeStorage()
    parent = make_parent(db_session)
    student = make_student(db_session)

    out = create_pickup_person(
        db_session, student.id, _data(), None, parent=_current(parent), storage=storage
    )

    row = db_session.execute(select(PickupPerson).where(PickupPerson.id == out.id)).scalar_one()
    assert out.photo_url is None
    assert row.photo_path is None
    assert storage.objects == {}


def test_create_pickup_person_limit(db_session: Session) -> None:
    storage = FakeStorage()
    parent = make_parent(db_session)
    student = make_student(db_session)
    other = make_student(db_session, name="林小安")
    _set_max_per_student(db_session, 2)
    make_pickup_person(db_session, student, name="甲")
    make_pickup_person(db_session, student, name="乙")
    archived = make_pickup_person(db_session, student, name="已封存")
    archived.archived_at = ARCHIVED_AT
    db_session.flush()

    with pytest.raises(AppError) as exc:
        create_pickup_person(
            db_session, student.id, _data(), None, parent=_current(parent), storage=storage
        )

    assert (exc.value.status, exc.value.code) == (409, "pickup_person_limit_reached")
    assert exc.value.details == {"max_per_student": 2}
    assert _person_count(db_session, student.id) == 3
    # 上限是每位學生各自計算
    create_pickup_person(
        db_session, other.id, _data(), None, parent=_current(parent), storage=storage
    )
    assert _person_count(db_session, other.id) == 1


def test_create_pickup_person_archived_do_not_count(db_session: Session) -> None:
    storage = FakeStorage()
    parent = make_parent(db_session)
    student = make_student(db_session)
    _set_max_per_student(db_session, 2)
    make_pickup_person(db_session, student, name="甲")
    archived = make_pickup_person(db_session, student, name="已封存")
    archived.archived_at = ARCHIVED_AT
    db_session.flush()

    out = create_pickup_person(
        db_session, student.id, _data("丙"), None, parent=_current(parent), storage=storage
    )

    assert out.name == "丙"


def test_create_pickup_person_file_errors(db_session: Session) -> None:
    storage = FakeStorage()
    parent = make_parent(db_session)
    student = make_student(db_session)
    pdf = UploadFile(file=io.BytesIO(_PDF), filename="a.pdf")

    with pytest.raises(AppError) as unsupported:
        create_pickup_person(
            db_session, student.id, _data(), pdf, parent=_current(parent), storage=storage
        )
    storage.upload_error = StorageError("S3 put_object 失敗")
    with pytest.raises(AppError) as unavailable:
        create_pickup_person(
            db_session, student.id, _data(), _jpeg(), parent=_current(parent), storage=storage
        )

    assert (unsupported.value.status, unsupported.value.code) == (415, "unsupported_file_type")
    assert (unavailable.value.status, unavailable.value.code) == (502, "storage_unavailable")
    assert _person_count(db_session, student.id) == 0
    assert storage.objects == {}


def test_create_pickup_person_insert_failure_removes_uploaded_photo(db_session: Session) -> None:
    storage = FakeStorage()
    parent = make_parent(db_session)

    # 不存在的 student_id：flush 時違反 FK，剛上傳的照片要被刪除，例外原樣拋出
    with pytest.raises(Exception, match="fk_pickup_persons_student_id"):
        create_pickup_person(
            db_session, uuid4(), _data(), _jpeg(), parent=_current(parent), storage=storage
        )

    assert storage.objects == {}


def test_archive_pickup_person(db_session: Session) -> None:
    storage = FakeStorage()
    clock = FakeClock(_NOW)
    parent = make_parent(db_session)
    student = make_student(db_session)
    make_guardian(db_session, student, parent=parent)
    photo = build_object_path(uuid4(), "jpg")
    storage.upload("pickup-person-photos", photo, _JPEG, "image/jpeg")
    person = make_pickup_person(db_session, student, photo_path=photo)
    keep = make_pickup_person(db_session, student, name="張伯伯")

    archive_pickup_person(db_session, person.id, parent=_current(parent), clock=clock)

    db_session.refresh(person)
    db_session.refresh(keep)
    assert person.archived_at == clock.now()
    assert keep.archived_at is None
    assert [p.name for p in list_pickup_persons(db_session, student.id, storage=storage)] == [
        "張伯伯"
    ]
    assert ("pickup-person-photos", photo) in storage.objects


def test_archive_pickup_person_idor(db_session: Session) -> None:
    clock = FakeClock(_NOW)
    parent_a = make_parent(db_session)
    parent_b = make_parent(db_session, display_name="林媽媽")
    child_a = make_student(db_session)
    child_b = make_student(db_session, name="林小安")
    make_guardian(db_session, child_a, parent=parent_a)
    make_guardian(db_session, child_b, parent=parent_b)
    theirs = make_pickup_person(db_session, child_b)
    mine = make_pickup_person(db_session, child_a)

    with pytest.raises(AppError) as other_family:
        archive_pickup_person(db_session, theirs.id, parent=_current(parent_a), clock=clock)
    with pytest.raises(AppError) as missing:
        archive_pickup_person(db_session, uuid4(), parent=_current(parent_a), clock=clock)
    archive_pickup_person(db_session, mine.id, parent=_current(parent_a), clock=clock)
    with pytest.raises(AppError) as again:
        archive_pickup_person(db_session, mine.id, parent=_current(parent_a), clock=clock)

    for exc in (other_family, missing, again):
        assert (exc.value.status, exc.value.code) == (404, "pickup_person_not_found")
    assert other_family.value.message == missing.value.message
    db_session.refresh(theirs)
    assert theirs.archived_at is None
