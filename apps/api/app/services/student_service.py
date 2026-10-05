"""BACKEND-149：學生服務（清單搜尋、篩選、分頁）。
BACKEND-150：``get_student`` 學生詳情（移植 ivy ``api/students.py::get_student`` /
``get_student_medical`` 的「敏感欄位另行授權」；去掉醫療多欄位、lifecycle）。

- 封存學生也可查（include_archived）；不存在 404 ``student_not_found``。
- ``has_id_number`` / ``has_health_note`` 一律回傳；``sensitive`` 只在 actor 有
  ``students:sensitive`` 時以 BACKEND-009 解密填入，否則 None；解密失敗 → 該欄位 None 並
  logger.error（只記學生 id 與欄位名，不記密文）。
- ``photo_url``：photo_path 有值時簽 300 秒短效 URL；StorageError → None 並記 log，不讓詳情頁失敗。
- ``guardians``：BACKEND-168 ``list_for_student``（未封存監護人、含綁定狀態；需要 clock 判斷綁定碼
  是否過期，故本函式多收 ``clock``）。

BACKEND-151 ``create_student``（移植 ivy ``api/students.py::create_student`` 的唯一檢查與
``services/student_dedup.py`` 的查重精神，改為身分證 HMAC）：
- 敏感欄位（id_number、health_note）的寫入需 ``students:sensitive``，否則 403
  ``sensitive_permission_required``；只填空白視為未給（schema strip 後為空字串，寫入前轉 null，
  school_class 亦同）。
- school_id 不存在或停用 → 422 ``invalid_school``；class_id 不存在或已封存 → 422 ``invalid_class``；
  withdrawn 未給退班日預設 ``clock.today()``；withdrawn_on 早於 enrolled_on → 422
  ``invalid_dates``。
- 身分證 normalize → validate → HMAC；同 HMAC 的既有學生（含封存）→ 409 ``id_number_duplicate``
  （details 指出既有學生，同一人再次入班應沿用原紀錄）；學號撞 unique → 409 ``student_no_taken``。
- 有寫入敏感欄位時稽核 ``student.sensitive_update``，after 只記欄位名
  ``{"set": [...]}``（audit_service 會把含 id_number / health_note 的 **key** 遮罩成 ``***``，故以
  list 記欄位名而不以欄位名當 key）。

BACKEND-153 ``archive_student``：封存（冪等）、刪除該生所有監護人的**未使用**綁定碼；封存後
BACKEND-179 的家長可見範圍自動排除。不限制 status。

BACKEND-154 ``upload_photo``：BACKEND-016 驗證 → ``build_object_path`` → upload（StorageError →
502 ``storage_unavailable``）→ 更新 photo_path（flush 失敗刪掉剛上傳的物件）→ 舊檔以
``run_after_commit`` 刪除（rollback 不刪、刪除失敗只記 log）→ 回傳短效 URL。

BACKEND-530 ``purge_student``（domain_spec M3 個資保存：對已封存且 withdrawn 的學生永久刪除 =
匿名化）：學生 / 監護人 / 接送人 / 代理授權的個資欄位清除或改成固定文字、綁定碼與請假附件列刪除、
含該生 student_id 的站內通知刪除；出勤、成績、接送、請假列保留（統計）只清自由文字。Storage 物件
在 commit 後刪除；稽核 ``student.purge`` 只記各類筆數，不含姓名 / 學號 / 電話。再次執行以既有的
``student.purge`` 稽核判定 → 409 ``student_already_purged``。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import date, datetime
from functools import partial
from typing import Any, Final, cast
from uuid import UUID, uuid4

from psycopg.errors import UniqueViolation
from sqlalchemy import CursorResult, delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from app.api.deps import CurrentStaff
from app.core.clock import Clock
from app.core.crypto import DecryptionError, decrypt_bytes, encrypt_bytes
from app.core.errors import AppError, ConflictError, ForbiddenError
from app.core.pagination import Page, PageParams, paginate
from app.core.permissions import Permission
from app.core.request_meta import RequestMeta
from app.core.storage import Bucket, Storage, StorageError, build_object_path
from app.core.tx_hooks import run_after_commit
from app.core.uploads import IMAGE_TYPES, PHOTO_MAX_BYTES, read_validated_upload
from app.models.attendance import StudentAttendance
from app.models.audit import AuditLog
from app.models.classes import SchoolClass
from app.models.exams import ExamScore
from app.models.leaves import StudentLeave, StudentLeaveAttachment
from app.models.notifications import Notification
from app.models.parents import Guardian, ParentBindingCode
from app.models.pickup import PickupAuthorization, PickupPerson
from app.models.reference import School
from app.models.students import Student
from app.repositories.students import get_student_or_404
from app.schemas.students import (
    PhotoUploadOut,
    StudentCreateIn,
    StudentDetailOut,
    StudentListItemOut,
    StudentListQuery,
    StudentPurgeIn,
    StudentPurgeOut,
    StudentSensitiveOut,
)
from app.services import audit_service
from app.services.guardian_service import list_for_student
from app.services.students.id_number import (
    id_number_hmac,
    normalize_id_number,
    validate_id_number,
)

logger = logging.getLogger(__name__)

PHOTO_URL_TTL_SECONDS = 300
PHOTO_BUCKET: Final[Bucket] = "student-photos"
LEAVE_ATTACHMENT_BUCKET: Final[Bucket] = "leave-attachments"
PICKUP_PHOTO_BUCKET: Final[Bucket] = "pickup-person-photos"
PURGED_STUDENT_NAME: Final = "已刪除學生"
PURGED_TEXT: Final = "已刪除"
PURGED_PHONE: Final = "00000000"  # 符合 DB CHECK 的電話格式
_STUDENT_NO_UNIQUE: Final = "uq_students_student_no"
_ID_NUMBER_UNIQUE: Final = "uq_students_id_number_hmac"


def list_students(
    session: Session, query: StudentListQuery, page: PageParams, *, actor: CurrentStaff
) -> Page[StudentListItemOut]:
    """q：學號前綴（不分大小寫）或姓名模糊；有 students:sensitive 且 q 是合法身分證時另比對 HMAC。

    沒有 sensitive 權限時不做身分證比對，避免以搜尋結果推測身分證。排序 grade_level、班級名稱
    （無班級在後）、student_no。預設排除封存。
    """
    stmt = select(Student).outerjoin(SchoolClass, SchoolClass.id == Student.class_id)
    if not query.include_archived:
        stmt = stmt.where(Student.archived_at.is_(None))
    if query.q:
        conditions = [
            Student.student_no.istartswith(query.q, autoescape=True),
            Student.name.icontains(query.q, autoescape=True),
        ]
        if actor.has(Permission.STUDENTS_SENSITIVE) and _is_id_number(query.q):
            conditions.append(
                Student.id_number_hmac == id_number_hmac(normalize_id_number(query.q))
            )
        stmt = stmt.where(or_(*conditions))
    if query.class_id is not None:
        stmt = stmt.where(Student.class_id == query.class_id)
    if query.grade_level is not None:
        stmt = stmt.where(Student.grade_level == query.grade_level)
    if query.status is not None:
        stmt = stmt.where(Student.status == query.status)
    if query.school_id is not None:
        stmt = stmt.where(Student.school_id == query.school_id)

    rows, total = paginate(
        session,
        stmt.order_by(
            Student.grade_level, SchoolClass.name.asc().nulls_last(), Student.student_no, Student.id
        ),
        page,
    )
    return Page(items=[StudentListItemOut.model_validate(row) for row in rows], total=total)


def _is_id_number(q: str) -> bool:
    try:
        validate_id_number(normalize_id_number(q))
    except AppError:
        return False
    return True


def _decrypt_field(student_id: UUID, field: str, blob: bytes | None) -> str | None:
    if blob is None:
        return None
    try:
        return decrypt_bytes(blob)
    except DecryptionError:
        logger.error("學生 %s 的 %s 解密失敗，視為未設定", student_id, field)
        return None


def _sensitive(student: Student, actor: CurrentStaff) -> StudentSensitiveOut | None:
    if not actor.has(Permission.STUDENTS_SENSITIVE):
        return None
    return StudentSensitiveOut(
        id_number=_decrypt_field(student.id, "id_number", student.id_number_enc),
        health_note=_decrypt_field(student.id, "health_note", student.health_note_enc),
    )


def _photo_url(student: Student, storage: Storage | None) -> str | None:
    if storage is None or not student.photo_path:
        return None
    try:
        return storage.create_signed_url(
            "student-photos", student.photo_path, PHOTO_URL_TTL_SECONDS
        )
    except StorageError:
        logger.warning("學生 %s 的照片簽名 URL 失敗，詳情頁不帶照片", student.id, exc_info=True)
        return None


def _detail_out(
    session: Session,
    student: Student,
    *,
    actor: CurrentStaff | None,
    storage: Storage | None,
    clock: Clock,
) -> StudentDetailOut:
    """actor 為 None → sensitive 一律 None；storage 為 None → photo_url 一律 None。"""
    base = StudentListItemOut.model_validate(student).model_dump(by_alias=True)
    return StudentDetailOut(
        **base,
        birthday=student.birthday,
        enrolled_on=student.enrolled_on,
        withdrawn_on=student.withdrawn_on,
        note=student.note,
        photo_url=_photo_url(student, storage),
        has_id_number=student.id_number_enc is not None,
        has_health_note=student.health_note_enc is not None,
        sensitive=None if actor is None else _sensitive(student, actor),
        guardians=list_for_student(session, student.id, clock=clock),
    )


def get_student(
    session: Session,
    student_id: UUID,
    *,
    actor: CurrentStaff,
    storage: Storage,
    clock: Clock,
) -> StudentDetailOut:
    student = get_student_or_404(session, student_id, include_archived=True)
    return _detail_out(session, student, actor=actor, storage=storage, clock=clock)


# --- BACKEND-151：create_student ------------------------------------------------------------------


def _blank_to_none(value: str | None) -> str | None:
    """schema 已 strip：只填空白會變成空字串，寫入前視為未給。"""
    return value or None


def _require_sensitive_permission(actor: CurrentStaff, touches_sensitive: bool) -> None:
    if touches_sensitive and not actor.has(Permission.STUDENTS_SENSITIVE):
        raise ForbiddenError(
            "身分證字號與健康備註需要 students:sensitive 權限才能寫入",
            code="sensitive_permission_required",
        )


def _check_school(session: Session, school_id: UUID | None) -> None:
    if school_id is None:
        return
    school = session.get(School, school_id)
    if school is None or not school.is_active:
        raise AppError("invalid_school", "就讀國小不存在或已停用", status=422)


def _check_class(session: Session, class_id: UUID | None) -> None:
    if class_id is None:
        return
    klass = session.get(SchoolClass, class_id)
    if klass is None or klass.archived_at is not None:
        raise AppError("invalid_class", "班級不存在或已封存", status=422)


def _check_dates(enrolled_on: date | None, withdrawn_on: date | None) -> None:
    if enrolled_on is not None and withdrawn_on is not None and withdrawn_on < enrolled_on:
        raise AppError("invalid_dates", "退班日不可早於入班日", status=422)


def _raise_if_id_number_exists(session: Session, hmac: str) -> None:
    """同 HMAC 的學生（含封存）已存在 → 409 ``id_number_duplicate``，details 指出既有學生。"""
    row = session.execute(
        select(Student.id, Student.student_no, Student.name).where(Student.id_number_hmac == hmac)
    ).one_or_none()
    if row is None:
        return
    raise ConflictError(
        "id_number_duplicate",
        "此身分證字號已有學生紀錄，請沿用原紀錄",
        details={"student_id": row.id, "student_no": row.student_no, "name": row.name},
    )


def _prepare_id_number(session: Session, raw: str) -> tuple[bytes, str]:
    normalized = normalize_id_number(raw)
    validate_id_number(normalized)
    hmac = id_number_hmac(normalized)
    _raise_if_id_number_exists(session, hmac)
    return encrypt_bytes(normalized), hmac


def _translate_student_unique(session: Session, exc: IntegrityError, hmac: str | None) -> None:
    """savepoint 已 rollback；學號撞 unique → 409 ``student_no_taken``，身分證（競態）→ 409。"""
    if getattr(exc.orig, "sqlstate", None) != UniqueViolation.sqlstate:
        raise exc
    detail = str(exc.orig)
    if _STUDENT_NO_UNIQUE in detail:
        raise ConflictError("student_no_taken", "學號已被使用") from None
    if _ID_NUMBER_UNIQUE in detail and hmac is not None:
        _raise_if_id_number_exists(session, hmac)
        raise ConflictError("id_number_duplicate", "此身分證字號已有學生紀錄") from None
    raise exc


def _record_sensitive_audit(
    session: Session,
    *,
    actor: CurrentStaff,
    student_id: UUID,
    after: dict[str, list[str]],
    meta: RequestMeta,
) -> None:
    audit_service.record(
        session,
        actor=audit_service.Actor.staff(actor),
        action="student.sensitive_update",
        entity_type="student",
        entity_id=student_id,
        after=after,
        meta=meta,
    )


def create_student(
    session: Session,
    data: StudentCreateIn,
    *,
    actor: CurrentStaff,
    meta: RequestMeta,
    clock: Clock,
) -> StudentDetailOut:
    id_number = _blank_to_none(data.id_number)
    health_note = _blank_to_none(data.health_note)
    _require_sensitive_permission(actor, id_number is not None or health_note is not None)
    _check_school(session, data.school_id)
    _check_class(session, data.class_id)
    withdrawn_on = data.withdrawn_on
    if data.status == "withdrawn" and withdrawn_on is None:
        withdrawn_on = clock.today()
    _check_dates(data.enrolled_on, withdrawn_on)

    id_number_enc: bytes | None = None
    id_number_hash: str | None = None
    if id_number is not None:
        id_number_enc, id_number_hash = _prepare_id_number(session, id_number)

    student = Student(
        student_no=data.student_no,
        name=data.name,
        gender=data.gender,
        birthday=data.birthday,
        grade_level=data.grade_level,
        school_id=data.school_id,
        school_class=_blank_to_none(data.school_class),
        class_id=data.class_id,
        status=data.status,
        enrolled_on=data.enrolled_on,
        withdrawn_on=withdrawn_on,
        note=data.note,
        id_number_enc=id_number_enc,
        id_number_hmac=id_number_hash,
        health_note_enc=encrypt_bytes(health_note) if health_note is not None else None,
    )
    try:
        with session.begin_nested():
            session.add(student)
            session.flush()
    except IntegrityError as exc:
        _translate_student_unique(session, exc, id_number_hash)

    written = sorted(
        name for name, value in (("id_number", id_number), ("health_note", health_note)) if value
    )
    if written:
        _record_sensitive_audit(
            session, actor=actor, student_id=student.id, after={"set": written}, meta=meta
        )
    return _detail_out(session, student, actor=actor, storage=None, clock=clock)


# --- BACKEND-153：archive_student -----------------------------------------------------------------


def archive_student(
    session: Session,
    student_id: UUID,
    *,
    clock: Clock,
    actor: CurrentStaff | None = None,
    storage: Storage | None = None,
) -> StudentDetailOut:
    """封存學生（冪等）；刪除該生所有監護人的未使用綁定碼（已使用的保留為綁定歷史）。

    ``actor`` / ``storage`` 選填：endpoint 傳入時回應才帶 sensitive / photo_url。
    """
    student = get_student_or_404(session, student_id, include_archived=True, for_update=True)
    if student.archived_at is None:
        student.archived_at = clock.now()
        session.execute(
            delete(ParentBindingCode).where(
                ParentBindingCode.used_at.is_(None),
                ParentBindingCode.guardian_id.in_(
                    select(Guardian.id).where(Guardian.student_id == student.id)
                ),
            )
        )
        session.flush()
    return _detail_out(session, student, actor=actor, storage=storage, clock=clock)


# --- BACKEND-154：upload_photo --------------------------------------------------------------------


def _delete_quietly(storage: Storage, paths: Sequence[str], bucket: Bucket = PHOTO_BUCKET) -> None:
    try:
        storage.delete(bucket, paths)
    except StorageError:
        logger.warning("刪除 %s 物件失敗（留下孤兒物件）：%s", bucket, list(paths), exc_info=True)


def upload_photo(
    session: Session, student_id: UUID, file: UploadFile, *, storage: Storage
) -> PhotoUploadOut:
    student = get_student_or_404(session, student_id)  # 封存學生視同不存在
    upload = read_validated_upload(file, allowed=IMAGE_TYPES, max_bytes=PHOTO_MAX_BYTES)
    path = build_object_path(student.id, upload.ext)
    try:
        storage.upload(PHOTO_BUCKET, path, upload.content, upload.mime_type)
    except StorageError as exc:
        raise AppError(
            "storage_unavailable", "檔案儲存服務暫時無法使用，請稍後再試", status=502
        ) from exc

    old_path = student.photo_path
    student.photo_path = path
    try:
        session.flush()
        url = storage.create_signed_url(PHOTO_BUCKET, path, PHOTO_URL_TTL_SECONDS)
    except (SQLAlchemyError, StorageError):
        # DB 寫不進去或簽不出 URL：剛上傳的物件沒人參照，直接刪掉再重新拋出
        _delete_quietly(storage, [path])
        raise
    if old_path:
        run_after_commit(session, lambda: _delete_quietly(storage, [old_path]))
    return PhotoUploadOut(photo_url=url)


# --- BACKEND-530：purge_student -------------------------------------------------------------------


def _rowcount(result: object) -> int:
    return int(cast(CursorResult[Any], result).rowcount or 0)


def _already_purged(session: Session, student_id: UUID) -> bool:
    return (
        session.execute(
            select(AuditLog.id)
            .where(AuditLog.action == "student.purge", AuditLog.entity_id == str(student_id))
            .limit(1)
        ).scalar_one_or_none()
        is not None
    )


def _anonymize_student(student: Student) -> None:
    """統計用欄位保留：grade_level、school_id、class_id、status、enrolled_on、withdrawn_on、
    archived_at。"""
    student.name = PURGED_STUDENT_NAME
    student.student_no = f"DEL-{uuid4().hex[:8]}"
    student.gender = None
    student.birthday = None
    student.school_class = None
    student.note = None
    student.photo_path = None
    student.id_number_enc = None
    student.id_number_hmac = None
    student.health_note_enc = None


def _purge_guardians(session: Session, student_id: UUID, now: datetime) -> int:
    guardian_ids = select(Guardian.id).where(Guardian.student_id == student_id)
    session.execute(
        delete(ParentBindingCode).where(ParentBindingCode.guardian_id.in_(guardian_ids))
    )
    # 家長帳號本身不刪（可能綁定其他小孩），只解除綁定
    return _rowcount(
        session.execute(
            update(Guardian)
            .where(Guardian.student_id == student_id)
            .values(
                name=PURGED_TEXT,
                phone=None,
                parent_account_id=None,
                is_primary=False,
                archived_at=func.coalesce(Guardian.archived_at, now),
            )
        )
    )


def _purge_pickup(session: Session, student_id: UUID, now: datetime) -> tuple[int, list[str]]:
    photo_paths = list(
        session.execute(
            select(PickupPerson.photo_path).where(
                PickupPerson.student_id == student_id, PickupPerson.photo_path.is_not(None)
            )
        ).scalars()
    )
    persons = _rowcount(
        session.execute(
            update(PickupPerson)
            .where(PickupPerson.student_id == student_id)
            .values(
                name=PURGED_TEXT,
                phone=PURGED_PHONE,
                photo_path=None,
                archived_at=func.coalesce(PickupPerson.archived_at, now),
            )
        )
    )
    session.execute(
        update(PickupAuthorization)
        .where(PickupAuthorization.student_id == student_id)
        .values(proxy_name=PURGED_TEXT, proxy_phone=PURGED_PHONE)
    )
    return persons, [str(path) for path in photo_paths]


def _purge_leaves(session: Session, student_id: UUID) -> list[str]:
    """請假列保留、reason 清空；附件列刪除並回傳其 storage path。"""
    leave_ids = select(StudentLeave.id).where(StudentLeave.student_id == student_id)
    attachment_paths = list(
        session.execute(
            select(StudentLeaveAttachment.storage_path).where(
                StudentLeaveAttachment.leave_id.in_(leave_ids)
            )
        ).scalars()
    )
    session.execute(
        delete(StudentLeaveAttachment).where(StudentLeaveAttachment.leave_id.in_(leave_ids))
    )
    session.execute(
        update(StudentLeave).where(StudentLeave.student_id == student_id).values(reason=None)
    )
    return attachment_paths


def _purge_free_text(session: Session, student_id: UUID) -> None:
    """出勤與成績列保留（統計），只清可能含姓名的自由文字。"""
    session.execute(
        update(StudentAttendance)
        .where(StudentAttendance.student_id == student_id)
        .values(note=None)
    )
    session.execute(update(ExamScore).where(ExamScore.student_id == student_id).values(note=None))


def _purge_notifications(session: Session, student_id: UUID) -> int:
    # outbox 隨 FK cascade 刪除
    return _rowcount(
        session.execute(
            delete(Notification).where(Notification.payload["student_id"].astext == str(student_id))
        )
    )


def purge_student(
    session: Session,
    student_id: UUID,
    data: StudentPurgeIn,
    *,
    actor: CurrentStaff,
    storage: Storage,
    meta: RequestMeta,
    clock: Clock,
) -> StudentPurgeOut:
    # endpoint 已守衛 students:purge，service 縱深防禦
    if not actor.has(Permission.STUDENTS_PURGE):
        raise ForbiddenError()
    student = get_student_or_404(session, student_id, include_archived=True, for_update=True)
    if student.archived_at is None or student.status != "withdrawn":
        raise ConflictError("student_not_purgeable", "只有已封存且已退班的學生可以永久刪除")
    if _already_purged(session, student.id):
        raise ConflictError("student_already_purged", "此學生已經永久刪除過")
    if data.confirm_student_no != student.student_no:
        raise AppError("purge_confirmation_mismatch", "確認學號與目前學號不符", status=422)

    now = clock.now()
    objects: dict[Bucket, list[str]] = {
        PHOTO_BUCKET: [student.photo_path] if student.photo_path else [],
    }
    _anonymize_student(student)
    guardians = _purge_guardians(session, student.id, now)
    pickup_persons, objects[PICKUP_PHOTO_BUCKET] = _purge_pickup(session, student.id, now)
    objects[LEAVE_ATTACHMENT_BUCKET] = _purge_leaves(session, student.id)
    _purge_free_text(session, student.id)
    notifications = _purge_notifications(session, student.id)
    session.flush()

    # Storage 只在 commit 成功後刪；commit 失敗（rollback）不刪，刪除失敗只記 log
    for bucket, paths in objects.items():
        if paths:
            run_after_commit(session, partial(_delete_quietly, storage, paths, bucket))

    audit_service.record(
        session,
        actor=audit_service.Actor.staff(actor),
        action="student.purge",
        entity_type="student",
        entity_id=student.id,
        after={
            "guardians": guardians,
            "pickup_persons": pickup_persons,
            "attachments": len(objects[LEAVE_ATTACHMENT_BUCKET]),
            "notifications": notifications,
        },
        meta=meta,
    )
    return StudentPurgeOut(
        student_id=student.id, purged_at=now, anonymized_student_no=student.student_no
    )
