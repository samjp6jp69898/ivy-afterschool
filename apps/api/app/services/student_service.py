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

BACKEND-154 ``upload_photo``：先 FOR UPDATE 鎖學生列（並發上傳序列化、重讀上鎖後的
photo_path）→ BACKEND-016 驗證 → ``build_object_path`` → upload（StorageError → 502
``storage_unavailable``）→ 更新 photo_path（flush 失敗刪掉剛上傳的物件）→ 舊檔以
``run_after_commit`` 刪除（rollback 不刪、刪除失敗只記 log）→ 回傳短效 URL。

BACKEND-530 ``purge_student``（domain_spec M3 個資保存：對已封存且 withdrawn 的學生永久刪除 =
匿名化）：學生 / 監護人 / 接送人 / 代理授權的個資欄位清除或改成固定文字、綁定碼與請假附件列刪除、
含該生 student_id 的站內通知刪除；出勤、成績、接送、請假列保留（統計）只清自由文字。Storage 物件
在 commit 後刪除；稽核 ``student.purge`` 只記各類筆數，不含姓名 / 學號 / 電話。再次執行以既有的
``student.purge`` 稽核判定 → 409 ``student_already_purged``。

BACKEND-529 ``close_out_inactive_student``（domain_spec M3：學生改為 suspended / withdrawn 時同交易
收尾，已發生的紀錄保留；由 BACKEND-152 / 158 呼叫，不 commit）。處理順序即鎖序（BACKEND-548，與
BACKEND-425 ``complete_via_authorization`` 的「授權列 → 請求列 → 出勤列」同向，避免 40P01）：今天起
active 代理授權 cancelled 並廣播 → 非終態接送請求條件式改 cancelled 並 ``publish_request_change`` →
請假（未開始整筆 cancelled、已開始截到昨天，對應出勤還原）→ 刪除今天起 ``expected`` 出勤並廣播
``attendance.bulk_updated`` → 稽核 ``student.close_out``（只記計數與新狀態）。不發通知。

BACKEND-152 ``update_student``（移植 ivy ``api/students.py::update_student`` 的部分更新與敏感欄位
守衛；去掉 lifecycle、租戶、銷帳碼）：先 FOR UPDATE 鎖學生列（封存或不存在 → 404），只處理
``model_fields_set`` 內的欄位。所有檢查都在改動 ORM 物件之前完成，失敗時 session 內沒有半套的
pending 變更：
- 敏感欄位（寫入或清除）需 ``students:sensitive``；給 null 或只填空白 → 同時清除 enc 與 hmac；
  改成同一個身分證不算變動；他人的身分證 → 409 ``id_number_duplicate``。有變動才稽核
  ``student.sensitive_update``，after ``{"set": [...], "cleared": [...]}`` 只放有變動的 key。
- 跨欄位規則以明確錯誤碼擋在 DB CHECK 之前（否則落成通用 409）：→ withdrawn 未給退班日預設
  ``clock.today()``、withdrawn → active / suspended 未給時清除退班日、退班中清除退班日或退班日早於
  入班日 → 422 ``invalid_dates``。
- 改班或改狀態都經 ``_check_class`` 對班級列取 FOR SHARE：明確指定班級（同 create）或改狀態後成為
  在學（active / suspended）而仍有班級時，封存進行中（FOR UPDATE）會在此等待、commit 後重讀
  ``archived_at`` 再判斷 → 422 ``invalid_class``。退班學生改回在學不改 class_id 不會觸發 FK 檢查，
  沒有這把鎖就會進入剛封存的班。
- status 由其他值改為 suspended / withdrawn → 同交易呼叫 ``close_out_inactive_student``；改回 active
  或其他欄位變更不收尾。學號撞 unique → 409 ``student_no_taken``。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
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
from app.models.pickup import OPEN_STATUSES, PickupAuthorization, PickupPerson, PickupRequest
from app.models.reference import School
from app.models.students import Student, StudentStatus
from app.realtime.publish import broadcast_after_commit
from app.repositories.students import get_student_or_404
from app.schemas.pickup import PickupAuthorizationOut
from app.schemas.students import (
    PhotoUploadOut,
    StudentCreateIn,
    StudentDetailOut,
    StudentListItemOut,
    StudentListQuery,
    StudentPurgeIn,
    StudentPurgeOut,
    StudentSensitiveOut,
    StudentUpdateIn,
)
from app.services import audit_service
from app.services.guardian_service import list_for_student
from app.services.leave_attendance import revert_attendance_for_leave

# 授權輸出欄位的組裝在 r8c 的 authorizations 模組（私有 helper），廣播資料形狀須與該模組一致
from app.services.pickup.authorizations import _base_fields as authorization_fields
from app.services.pickup.views import publish_request_change
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
    """班級必須存在且未封存。先對班級列取 FOR SHARE：封存（FOR UPDATE）進行中時在此等待，
    commit 後重讀到 archived_at 才判斷，學生不會被放進剛封存的班。"""
    if class_id is None:
        return
    klass = session.execute(
        select(SchoolClass)
        .where(SchoolClass.id == class_id)
        .with_for_update(read=True, of=SchoolClass)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
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


def insert_student(
    session: Session,
    data: StudentCreateIn,
    *,
    actor: CurrentStaff,
    meta: RequestMeta,
    clock: Clock,
) -> Student:
    """BACKEND-151 的寫入邏輯（``create_student`` 與 BACKEND-156 匯入共用）：權限 / 參照 / 日期
    檢查、身分證查重與加密、savepoint 內 insert 並轉譯 unique、敏感欄位稽核；只回 ORM 物件、
    不組回應。"""
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
    return student


def create_student(
    session: Session,
    data: StudentCreateIn,
    *,
    actor: CurrentStaff,
    meta: RequestMeta,
    clock: Clock,
) -> StudentDetailOut:
    student = insert_student(session, data, actor=actor, meta=meta, clock=clock)
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
    # 封存學生視同不存在；FOR UPDATE 鎖學生列後才讀 photo_path：並發上傳序列化，後到者會把前者的
    # 物件當舊檔刪掉，不留孤兒
    student = get_student_or_404(session, student_id, for_update=True)
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


# --- BACKEND-529：close_out_inactive_student ------------------------------------------------------


@dataclass(frozen=True)
class CloseOutResult:
    cancelled_pickup_requests: int
    cancelled_authorizations: int
    cancelled_leaves: int
    truncated_leaves: int
    deleted_attendance_dates: list[date]


_CLOSE_OUT_REASONS: Final = {"suspended": "學生已停讀", "withdrawn": "學生已退班"}


def _close_out_leaves(
    session: Session, student: Student, *, actor: CurrentStaff, clock: Clock
) -> tuple[int, int]:
    """未開始 → cancelled（還原全部出勤）；已開始 → end_date 截到昨天（還原今天起的出勤）。"""
    today = clock.today()
    leaves = session.execute(
        select(StudentLeave).where(
            StudentLeave.student_id == student.id,
            StudentLeave.status == "active",
            StudentLeave.end_date >= today,
        )
    ).scalars()
    cancelled = truncated = 0
    for leave in leaves:
        if leave.start_date >= today:
            leave.status = "cancelled"
            leave.cancelled_at = clock.now()
            leave.cancelled_by_type = "staff"
            leave.cancelled_by_id = actor.id
            revert_attendance_for_leave(session, leave, clock=clock)
            cancelled += 1
        else:
            leave.end_date = today - timedelta(days=1)
            revert_attendance_for_leave(session, leave, from_date=today, clock=clock)
            truncated += 1
    session.flush()
    return cancelled, truncated


def _close_out_attendance(session: Session, student: Student, *, clock: Clock) -> list[date]:
    dates = sorted(
        session.execute(
            delete(StudentAttendance)
            .where(
                StudentAttendance.student_id == student.id,
                StudentAttendance.service_date >= clock.today(),
                StudentAttendance.status == "expected",
            )
            .returning(StudentAttendance.service_date)
        ).scalars()
    )
    if dates:
        broadcast_after_commit(
            session,
            topic="attendance",
            type="attendance.bulk_updated",
            data={"student_id": student.id, "dates": dates},
            clock=clock,
        )
    return dates


def _close_out_pickup_requests(session: Session, student: Student, *, clock: Clock) -> int:
    reason = _CLOSE_OUT_REASONS[student.status]
    ids = list(
        session.execute(
            update(PickupRequest)
            .where(
                PickupRequest.student_id == student.id,
                PickupRequest.status.in_(list(OPEN_STATUSES)),
            )
            .values(status="cancelled", cancelled_at=clock.now(), cancel_reason=reason)
            .returning(PickupRequest.id)
        ).scalars()
    )
    for request in session.execute(
        select(PickupRequest)
        .where(PickupRequest.id.in_(ids))
        .execution_options(populate_existing=True)
    ).scalars():
        publish_request_change(session, request, clock=clock)
    return len(ids)


def _close_out_authorizations(session: Session, student: Student, *, clock: Clock) -> int:
    today = clock.today()
    ids = list(
        session.execute(
            update(PickupAuthorization)
            .where(
                PickupAuthorization.student_id == student.id,
                PickupAuthorization.status == "active",
                PickupAuthorization.service_date >= today,
            )
            .values(status="cancelled")
            .returning(PickupAuthorization.id)
        ).scalars()
    )
    for auth in session.execute(
        select(PickupAuthorization)
        .where(PickupAuthorization.id.in_(ids))
        .execution_options(populate_existing=True)
    ).scalars():
        broadcast_after_commit(
            session,
            topic="pickup",
            type="pickup.authorization_updated",
            data=PickupAuthorizationOut(**authorization_fields(auth, today)).model_dump(),
            clock=clock,
        )
    return len(ids)


def close_out_inactive_student(
    session: Session, student: Student, *, actor: CurrentStaff, clock: Clock
) -> CloseOutResult:
    """學生已改為 suspended / withdrawn 後呼叫；與呼叫端同交易，任何一步失敗整筆回滾。

    鎖序固定為「授權列 → 請求列 → 請假 / 出勤列」（全專案約定：代理授權列 → 接送請求列 → 出勤列；
    請假列 → 出勤列），與櫃台核銷同向；順序反過來會與 complete_via_authorization 互等死結。
    """
    if student.status not in _CLOSE_OUT_REASONS:
        raise ValueError(f"close_out 只適用 suspended / withdrawn 學生：{student.status!r}")
    cancelled_authorizations = _close_out_authorizations(session, student, clock=clock)
    cancelled_requests = _close_out_pickup_requests(session, student, clock=clock)
    cancelled_leaves, truncated_leaves = _close_out_leaves(
        session, student, actor=actor, clock=clock
    )
    deleted_dates = _close_out_attendance(session, student, clock=clock)
    result = CloseOutResult(
        cancelled_pickup_requests=cancelled_requests,
        cancelled_authorizations=cancelled_authorizations,
        cancelled_leaves=cancelled_leaves,
        truncated_leaves=truncated_leaves,
        deleted_attendance_dates=deleted_dates,
    )
    audit_service.record(
        session,
        actor=audit_service.Actor.staff(actor),
        action="student.close_out",
        entity_type="student",
        entity_id=student.id,
        after={
            "status": student.status,
            "cancelled_pickup_requests": result.cancelled_pickup_requests,
            "cancelled_authorizations": result.cancelled_authorizations,
            "cancelled_leaves": result.cancelled_leaves,
            "truncated_leaves": result.truncated_leaves,
            "deleted_attendance_dates": result.deleted_attendance_dates,
        },
        meta=None,
    )
    return result


# --- BACKEND-152：update_student ------------------------------------------------------------------

_ENROLLED_STATUSES: Final = ("active", "suspended")
_SENSITIVE_FIELDS: Final = frozenset({"id_number", "health_note"})
# 直接 setattr 的一般欄位；status / withdrawn_on / school_class / 敏感欄位各有專屬規則
_PLAIN_FIELDS: Final = frozenset(
    {
        "student_no",
        "name",
        "gender",
        "birthday",
        "grade_level",
        "school_id",
        "class_id",
        "enrolled_on",
        "note",
    }
)


def _resolve_status_dates(
    student: Student, data: StudentUpdateIn, fields: frozenset[str] | set[str], clock: Clock
) -> tuple[StudentStatus, bool, date | None, date | None]:
    """回 ``(new_status, status_changed, enrolled_on, withdrawn_on)``。

    → withdrawn 未給退班日預設今天；withdrawn → active / suspended 未給時清除；退班學生沒有退班日
    或退班日早於入班日 → 422 ``invalid_dates``（擋在 DB CHECK 之前）。
    """
    # status 不在 nullable_fields：有給就是有值
    new_status: StudentStatus = data.status if data.status is not None else student.status
    status_changed = new_status != student.status
    enrolled_on = data.enrolled_on if "enrolled_on" in fields else student.enrolled_on
    if "withdrawn_on" in fields:
        withdrawn_on = data.withdrawn_on
    elif status_changed and new_status == "withdrawn":
        withdrawn_on = clock.today()
    elif status_changed and student.status == "withdrawn":
        withdrawn_on = None
    else:
        withdrawn_on = student.withdrawn_on
    if new_status == "withdrawn" and withdrawn_on is None:
        raise AppError("invalid_dates", "退班學生必須有退班日", status=422)
    _check_dates(enrolled_on, withdrawn_on)
    return new_status, status_changed, enrolled_on, withdrawn_on


def _plan_sensitive_update(
    session: Session, student: Student, data: StudentUpdateIn, fields: frozenset[str] | set[str]
) -> tuple[dict[str, Any], dict[str, list[str]]]:
    """回 ``(要寫入的欄位值, 稽核 after)``；沒有變動的欄位不出現在 after（after 為空 → 不稽核）。"""
    values: dict[str, Any] = {}
    set_fields: list[str] = []
    cleared: list[str] = []
    if "id_number" in fields:
        raw = _blank_to_none(data.id_number)
        if raw is None:
            if student.id_number_enc is not None or student.id_number_hmac is not None:
                cleared.append("id_number")
            values.update(id_number_enc=None, id_number_hmac=None)
        else:
            normalized = normalize_id_number(raw)
            validate_id_number(normalized)
            hmac = id_number_hmac(normalized)
            if hmac != student.id_number_hmac:
                _raise_if_id_number_exists(session, hmac)
                values.update(id_number_enc=encrypt_bytes(normalized), id_number_hmac=hmac)
                set_fields.append("id_number")
    if "health_note" in fields:
        raw = _blank_to_none(data.health_note)
        if raw is None:
            if student.health_note_enc is not None:
                cleared.append("health_note")
            values["health_note_enc"] = None
        elif raw != _decrypt_field(student.id, "health_note", student.health_note_enc):
            # 解密失敗視為不同（覆寫成可解的新密文）
            values["health_note_enc"] = encrypt_bytes(raw)
            set_fields.append("health_note")
    after = {
        key: sorted(names) for key, names in (("set", set_fields), ("cleared", cleared)) if names
    }
    return values, after


def update_student(
    session: Session,
    student_id: UUID,
    data: StudentUpdateIn,
    *,
    actor: CurrentStaff,
    meta: RequestMeta,
    clock: Clock,
) -> StudentDetailOut:
    fields = data.model_fields_set
    _require_sensitive_permission(actor, not fields.isdisjoint(_SENSITIVE_FIELDS))
    # 封存學生視同不存在；FOR UPDATE 讓同一學生的更新序列化，並重讀上鎖後的現值
    student = get_student_or_404(session, student_id, for_update=True)
    if "school_id" in fields:
        _check_school(session, data.school_id)
    new_status, status_changed, enrolled_on, withdrawn_on = _resolve_status_dates(
        student, data, fields, clock
    )
    class_id = data.class_id if "class_id" in fields else student.class_id
    # 明確指定班級（同 create），或改狀態後成為在學而仍有班級：FOR SHARE 等待封存 commit 後再判斷
    becomes_enrolled = status_changed and new_status in _ENROLLED_STATUSES
    if class_id is not None and (data.class_id is not None or becomes_enrolled):
        _check_class(session, class_id)
    sensitive_values, sensitive_after = _plan_sensitive_update(session, student, data, fields)

    changes: dict[str, Any] = data.model_dump(exclude_unset=True, include=set(_PLAIN_FIELDS))
    if "school_class" in fields:
        changes["school_class"] = _blank_to_none(data.school_class)
    changes.update(status=new_status, enrolled_on=enrolled_on, withdrawn_on=withdrawn_on)
    changes.update(sensitive_values)
    try:
        with session.begin_nested():
            for name, value in changes.items():
                setattr(student, name, value)
            session.flush()
    except IntegrityError as exc:
        _translate_student_unique(session, exc, sensitive_values.get("id_number_hmac"))
    if "class_id" in fields or "school_id" in fields:
        # 直接改 FK 欄位不會同步 lazy="joined" 的關係屬性，讓回傳的詳情重新載入班級 / 國小
        session.expire(student, ["class_", "school"])

    if status_changed and new_status in _CLOSE_OUT_REASONS:
        close_out_inactive_student(session, student, actor=actor, clock=clock)
    if sensitive_after:
        _record_sensitive_audit(
            session, actor=actor, student_id=student.id, after=sensitive_after, meta=meta
        )
    return _detail_out(session, student, actor=actor, storage=None, clock=clock)
