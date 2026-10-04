"""BACKEND-417 / 418 / 419：常用接送人（domain_spec M7）。

三個方法都由呼叫端先驗證所有權（417、418 以 path 的 student_id 經 ``get_owned_student*``）；
419 只收 person_id，自己以 ``get_parent_student_ids`` 限制家長可見範圍。照片放
``pickup-person-photos`` 分區，path 以 person id 為前綴；封存不刪照片（既有代理授權的照片比對仍
需要）。
"""

from __future__ import annotations

import logging
from typing import Final
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from app.api.deps import CurrentParent
from app.core.clock import Clock
from app.core.errors import AppError, ConflictError, NotFoundError
from app.core.settings_registry import PICKUP_PERSONS
from app.core.storage import Bucket, Storage, StorageError, build_object_path
from app.core.uploads import IMAGE_TYPES, PHOTO_MAX_BYTES, read_validated_upload
from app.models.pickup import PickupPerson
from app.schemas.pickup import PickupPersonCreateIn, PickupPersonOut
from app.services.parent_scope import get_parent_student_ids
from app.services.settings_service import get_setting

logger = logging.getLogger(__name__)

PHOTO_BUCKET: Final[Bucket] = "pickup-person-photos"
PHOTO_URL_SECONDS: Final = 300


def _photo_url(storage: Storage, person: PickupPerson) -> str | None:
    if person.photo_path is None:
        return None
    try:
        return storage.create_signed_url(PHOTO_BUCKET, person.photo_path, PHOTO_URL_SECONDS)
    except StorageError:
        logger.warning("接送人照片簽名失敗 person_id=%s", person.id)
        return None


def _to_out(person: PickupPerson, storage: Storage) -> PickupPersonOut:
    return PickupPersonOut(
        id=person.id,
        student_id=person.student_id,
        name=person.name,
        relation=person.relation,
        phone=person.phone,
        photo_url=_photo_url(storage, person),
        created_at=person.created_at,
    )


def list_pickup_persons(
    session: Session, student_id: UUID, *, storage: Storage
) -> list[PickupPersonOut]:
    persons = session.execute(
        select(PickupPerson)
        .where(PickupPerson.student_id == student_id, PickupPerson.archived_at.is_(None))
        .order_by(PickupPerson.created_at, PickupPerson.id)
    ).scalars()
    return [_to_out(person, storage) for person in persons]


def create_pickup_person(
    session: Session,
    student_id: UUID,
    data: PickupPersonCreateIn,
    photo: UploadFile | None,
    *,
    parent: CurrentParent,
    storage: Storage,
) -> PickupPersonOut:
    max_per_student = get_setting(session, PICKUP_PERSONS).max_per_student
    active_count = session.execute(
        select(func.count())
        .select_from(PickupPerson)
        .where(PickupPerson.student_id == student_id, PickupPerson.archived_at.is_(None))
    ).scalar_one()
    if active_count >= max_per_student:
        raise ConflictError(
            "pickup_person_limit_reached",
            f"每位學生最多登記 {max_per_student} 位常用接送人",
            details={"max_per_student": max_per_student},
        )

    person_id = uuid4()
    photo_path: str | None = None
    if photo is not None:
        upload = read_validated_upload(photo, allowed=IMAGE_TYPES, max_bytes=PHOTO_MAX_BYTES)
        photo_path = build_object_path(person_id, upload.ext)
        try:
            storage.upload(PHOTO_BUCKET, photo_path, upload.content, upload.mime_type)
        except StorageError as exc:
            raise AppError(
                "storage_unavailable", "檔案儲存服務暫時無法使用，請稍後再試", status=502
            ) from exc

    person = PickupPerson(
        id=person_id,
        student_id=student_id,
        name=data.name,
        relation=data.relation,
        phone=data.phone,
        photo_path=photo_path,
        created_by_parent_id=parent.id,
    )
    try:
        session.add(person)
        session.flush()
    except Exception:
        if photo_path is not None:
            _delete_photo_quietly(storage, photo_path)
        raise
    return _to_out(person, storage)


def _delete_photo_quietly(storage: Storage, photo_path: str) -> None:
    try:
        storage.delete(PHOTO_BUCKET, [photo_path])
    except StorageError:
        logger.warning("接送人照片清理失敗 path=%s", photo_path)


def archive_pickup_person(
    session: Session, person_id: UUID, *, parent: CurrentParent, clock: Clock
) -> None:
    """軟刪除：他人的、不存在、已封存一律同一個 404；照片保留，既有 active 授權不受影響。"""
    person = session.execute(
        select(PickupPerson).where(
            PickupPerson.id == person_id,
            PickupPerson.archived_at.is_(None),
            PickupPerson.student_id.in_(get_parent_student_ids(session, parent.id)),
        )
    ).scalar_one_or_none()
    if person is None:
        raise NotFoundError("pickup_person_not_found", "找不到接送人")
    person.archived_at = clock.now()
    session.flush()
