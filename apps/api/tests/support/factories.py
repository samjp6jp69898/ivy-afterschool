"""BACKEND-022：整合測試用 SQLAlchemy 測資 factory（service / endpoint 測試建立測資的唯一入口）。

與 DB-002 的 ``tests/integration/db/factories.py``（psycopg、給 migration 測試）分工：本檔用 ORM。
全部透過 INFRA-010 的 ``db_session``（app_backend 角色）寫入，``session.add`` + ``flush`` 後回傳
ORM 物件、不 commit；factory 能寫入即代表各表對 app_backend 的 grant 正確，禁止 fallback 到 owner。

- 唯一欄位（username、student_no、班名、校名、角色 code）預設以「本行程隨機前綴 + 模組層遞增
  序號」產生：同一測試多次呼叫不衝突，committing 測試被中斷而殘留的列也不會撞到下一次執行。
- 預設值擬真但非真實個資（王小明、林老師、王媽媽）。
- 營運模組新增自己的 factory（``make_leave``、``make_attendance`` 等）時加在本檔。
"""

from __future__ import annotations

import itertools
import secrets
from collections.abc import Sequence
from datetime import UTC, date, datetime, time
from typing import Final
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security.passwords import hash_password
from app.models.account import Role, StaffUser
from app.models.classes import ClassStaff, ClassStaffRole, SchoolClass
from app.models.homework import (
    HomeworkDailyProgress,
    HomeworkItem,
    HomeworkItemStatus,
    OverallStatus,
)
from app.models.leaves import (
    LeaveActorType,
    LeaveStatus,
    LeaveType,
    StudentLeave,
    StudentLeaveAttachment,
)
from app.models.parents import Guardian, GuardianRelation, ParentAccount, ParentStatus
from app.models.pickup import (
    AuthorizationStatus,
    PickupAuthorization,
    PickupPerson,
    PickupRequest,
    ReplySource,
    RequestSource,
    RequestStatus,
)
from app.models.reference import School, Subject
from app.models.students import Student, StudentStatus
from app.services.pickup.codes import hash_pickup_code, pickup_code_last4

SYSTEM_ROLE_CODES: Final = frozenset({"admin", "director", "clerk", "tutor"})
# 封存時間固定（ruff 禁 datetime.now；測試不依賴真實時間）
ARCHIVED_AT: Final = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)

_seq = itertools.count(1)
_RUN: Final = secrets.token_hex(2)
# 同一明文只做一次 argon2 雜湊（每次約數十毫秒），測試批量建帳號時省時間
_password_hash_cache: dict[str, str] = {}


def _next() -> int:
    return next(_seq)


def _cached_hash(password: str) -> str:
    hashed = _password_hash_cache.get(password)
    if hashed is None:
        hashed = hash_password(password)
        _password_hash_cache[password] = hashed
    return hashed


def _add(session: Session, obj: object) -> None:
    session.add(obj)
    session.flush()


def make_role(
    session: Session,
    *,
    code: str | None = None,
    permissions: Sequence[str] = (),
    name: str | None = None,
    is_system: bool = False,
) -> Role:
    n = _next()
    role = Role(
        code=code or f"test_{_RUN}_{n}",
        name=name or f"測試角色 {n}",
        permissions=list(permissions),
        is_system=is_system,
    )
    _add(session, role)
    return role


def make_staff(
    session: Session,
    *,
    permissions: Sequence[str] | None = None,
    role_code: str | None = None,
    username: str | None = None,
    password: str = "Passw0rd-Test1",  # noqa: S107  測試固定密碼
    display_name: str = "林老師",
    is_active: bool = True,
    must_change_password: bool = False,
    extra_permissions: Sequence[str] = (),
    revoked_permissions: Sequence[str] = (),
) -> StaffUser:
    """permissions 給值 → 只含這些權限碼的自訂角色；role_code 給值 → seed 系統角色；
    兩者皆無 → 空權限角色。兩者同時給視為呼叫錯誤。"""
    if permissions is not None and role_code is not None:
        raise ValueError("make_staff 的 permissions 與 role_code 只能擇一")
    if role_code is not None:
        if role_code not in SYSTEM_ROLE_CODES:
            raise ValueError(f"role_code 必須是系統角色 {sorted(SYSTEM_ROLE_CODES)}：{role_code!r}")
        role = session.execute(select(Role).where(Role.code == role_code)).scalar_one()
    else:
        role = make_role(session, permissions=permissions or ())
    staff = StaffUser(
        username=username or f"staff-{_RUN}-{_next():04d}",
        password_hash=_cached_hash(password),
        display_name=display_name,
        role=role,
        is_active=is_active,
        must_change_password=must_change_password,
        extra_permissions=list(extra_permissions),
        revoked_permissions=list(revoked_permissions),
    )
    _add(session, staff)
    return staff


def make_parent(
    session: Session,
    *,
    line_user_id: str | None = None,
    display_name: str = "王媽媽",
    status: ParentStatus = "active",
) -> ParentAccount:
    parent = ParentAccount(
        line_user_id=line_user_id or f"U{uuid4().hex}",
        display_name=display_name,
        status=status,
    )
    _add(session, parent)
    return parent


def make_school(session: Session, *, name: str | None = None) -> School:
    school = School(name=name or f"測試國小 {_RUN}-{_next()}")
    _add(session, school)
    return school


def make_class(
    session: Session,
    *,
    name: str | None = None,
    grade_levels: Sequence[int] = (1, 2),
    academic_year: int = 115,
    archived: bool = False,
) -> SchoolClass:
    klass = SchoolClass(
        name=name or f"測試班 {_RUN}-{_next()}",
        grade_levels=list(grade_levels),
        academic_year=academic_year,
        archived_at=ARCHIVED_AT if archived else None,
    )
    _add(session, klass)
    return klass


def make_student(
    session: Session,
    *,
    name: str = "王小明",
    student_no: str | None = None,
    grade_level: int = 3,
    class_: SchoolClass | None = None,
    status: StudentStatus = "active",
    archived: bool = False,
    school: School | None = None,
) -> Student:
    student = Student(
        student_no=student_no or f"S{_RUN}-{_next():04d}",
        name=name,
        grade_level=grade_level,
        class_=class_,
        school=school,
        status=status,
        archived_at=ARCHIVED_AT if archived else None,
    )
    _add(session, student)
    return student


def make_guardian(
    session: Session,
    student: Student,
    *,
    parent: ParentAccount | None = None,
    name: str = "王媽媽",
    relation: GuardianRelation = "mother",
    is_primary: bool = False,
    can_pickup: bool = True,
    receives_notifications: bool = True,
    archived: bool = False,
) -> Guardian:
    guardian = Guardian(
        student_id=student.id,
        parent_account_id=parent.id if parent is not None else None,
        name=name,
        relation=relation,
        is_primary=is_primary,
        can_pickup=can_pickup,
        receives_notifications=receives_notifications,
        archived_at=ARCHIVED_AT if archived else None,
    )
    _add(session, guardian)
    return guardian


def make_class_staff(
    session: Session, class_: SchoolClass, staff: StaffUser, *, role: ClassStaffRole = "lead"
) -> ClassStaff:
    link = ClassStaff(class_id=class_.id, staff_user_id=staff.id, role=role)
    _add(session, link)
    return link


_ATTACHMENT_MIME: Final = {
    "jpg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
    "heic": "image/heic",
    "pdf": "application/pdf",
}


def make_leave(
    session: Session,
    student: Student,
    *,
    start_date: date,
    end_date: date | None = None,
    leave_type: LeaveType = "sick",
    status: LeaveStatus = "active",
    created_by_type: LeaveActorType = "staff",
    created_by_id: UUID | None = None,
    reason: str | None = None,
) -> StudentLeave:
    """end_date 預設等於 start_date；status='cancelled' 時自動補 cancelled_* 三欄。"""
    creator = created_by_id or uuid4()
    leave = StudentLeave(
        student_id=student.id,
        leave_type=leave_type,
        start_date=start_date,
        end_date=end_date or start_date,
        reason=reason,
        status=status,
        created_by_type=created_by_type,
        created_by_id=creator,
    )
    if status == "cancelled":
        leave.cancelled_at = ARCHIVED_AT
        leave.cancelled_by_type = created_by_type
        leave.cancelled_by_id = creator
    _add(session, leave)
    return leave


def make_leave_attachment(
    session: Session, leave: StudentLeave, *, ext: str = "pdf"
) -> StudentLeaveAttachment:
    attachment = StudentLeaveAttachment(
        leave_id=leave.id,
        storage_path=f"{leave.id}/{uuid4().hex}.{ext}",
        mime_type=_ATTACHMENT_MIME[ext],
        size_bytes=1024,
    )
    _add(session, attachment)
    return attachment


def make_homework_item(
    session: Session,
    student: Student,
    *,
    service_date: date,
    title: str = "數學習作 p.12-13",
    status: HomeworkItemStatus = "todo",
    subject: Subject | None = None,
    sort_order: int = 0,
) -> HomeworkItem:
    item = HomeworkItem(
        student_id=student.id,
        service_date=service_date,
        title=title,
        status=status,
        subject_id=subject.id if subject is not None else None,
        sort_order=sort_order,
    )
    _add(session, item)
    return item


def make_homework_progress(
    session: Session,
    student: Student,
    *,
    service_date: date,
    overall_status: OverallStatus = "not_started",
    ready_eta: time | None = None,
    note: str | None = None,
) -> HomeworkDailyProgress:
    """給 ready_eta 時自動補 eta_updated_at（滿足 ck_homework_daily_progress_eta_audit）。"""
    progress = HomeworkDailyProgress(
        student_id=student.id,
        service_date=service_date,
        overall_status=overall_status,
        ready_eta=ready_eta,
        eta_updated_at=ARCHIVED_AT if ready_eta is not None else None,
        note=note,
    )
    _add(session, progress)
    return progress


def make_pickup_person(
    session: Session,
    student: Student,
    *,
    name: str = "李阿姨",
    relation: str = "阿姨",
    phone: str = "0912-000-101",
    photo_path: str | None = None,
) -> PickupPerson:
    person = PickupPerson(
        student_id=student.id, name=name, relation=relation, phone=phone, photo_path=photo_path
    )
    _add(session, person)
    return person


def make_pickup_authorization(
    session: Session,
    student: Student,
    *,
    service_date: date,
    code: str = "123456",
    person: PickupPerson | None = None,
    proxy_name: str = "李阿姨",
    proxy_phone: str = "0912-000-101",
    status: AuthorizationStatus = "active",
    code_attempts: int = 0,
) -> PickupAuthorization:
    """code_hash 以 HMAC 計算，呼叫端的測試需要有 APP_SECRET_KEY 環境（見各測試的 env fixture）。

    code_attempts=5 時補 code_locked_at；status='completed' 時補 verified_at / verification_method。
    """
    auth = PickupAuthorization(
        student_id=student.id,
        service_date=service_date,
        pickup_person_id=person.id if person is not None else None,
        proxy_name=proxy_name,
        proxy_phone=proxy_phone,
        code_hash=hash_pickup_code(code),
        code_last4=pickup_code_last4(code),
        code_attempts=code_attempts,
        code_locked_at=ARCHIVED_AT if code_attempts == 5 else None,
        status=status,
    )
    if status == "completed":
        auth.verified_at = ARCHIVED_AT
        auth.verification_method = "code"
    _add(session, auth)
    return auth


def make_pickup_request(
    session: Session,
    student: Student,
    *,
    service_date: date,
    status: RequestStatus = "pending",
    source: RequestSource = "parent",
    requested_by: UUID | None = None,
    expected_arrival_at: datetime | None = None,
    reply_source: ReplySource | None = None,
    reply_message: str | None = None,
    reply_ready_eta: time | None = None,
) -> PickupRequest:
    """依 status 補 arrived_at / completed_at + completion_method='override' / cancelled_at；
    reply_source='staff' 時補 replied_by（另建一位員工）與 replied_at，'auto' 只補 replied_at。"""
    request = PickupRequest(
        student_id=student.id,
        service_date=service_date,
        source=source,
        requested_by_type="staff" if source == "staff" else "parent",
        requested_by_id=requested_by or uuid4(),
        expected_arrival_at=expected_arrival_at,
        status=status,
        reply_source=reply_source,
        reply_message=reply_message,
        reply_ready_eta=reply_ready_eta,
    )
    if status == "arrived":
        request.arrived_at = ARCHIVED_AT
    elif status == "completed":
        request.completed_at = ARCHIVED_AT
        request.completion_method = "override"
    elif status == "cancelled":
        request.cancelled_at = ARCHIVED_AT
    if reply_source is not None:
        request.replied_at = ARCHIVED_AT
    if reply_source == "staff":
        request.replied_by = make_staff(session).id
    _add(session, request)
    return request
