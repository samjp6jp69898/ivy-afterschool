"""BACKEND-087：員工帳號服務 ``list_staff_users``（移植 ivy ``api/auth.py::list_users``；去掉
tenant、employee 連動、家長列可見性，改為分頁 + 搜尋 + 篩選）。

- q：username 或 display_name 不分大小寫模糊比對，``%`` ``_`` 以 autoescape 當字面值。
- role_id、is_active 篩選；排序 ``is_active desc, username``（啟用帳號在前）。
- effective_permissions 以 BACKEND-072 ``resolve_effective_permissions`` 計算並排序；
  ``StaffUser.role`` 為 lazy='joined'，列表不 N+1。

BACKEND-527：``list_staff_options``：啟用員工的下拉選項（班級負責老師指派用，clerk 沒有 staff:read
也要能用），只回 id 與 display_name，依 display_name、username 排序，不分頁。

BACKEND-088 ``get_staff_user``：停用帳號也可查；不存在 404 ``staff_user_not_found``。
BACKEND-089 ``create_staff_user``（移植 ivy ``api/auth.py::create_user``；去掉 tenant、employee
連動、client 指定密碼）：username 轉小寫、重複 409 ``username_taken``；role_id 不存在 422
``invalid_role``；extra / revoked 經 BACKEND-074 ``assert_valid_permission_codes``（422）；新帳號
有效權限必須 ⊆ actor 有效權限（``assert_can_grant`` → 403 ``cannot_grant_permissions``，防提權）；
密碼為 ``generate_temp_password()`` 雜湊存入、``must_change_password = true``，明碼只在回傳值出現
一次；稽核 ``staff_user.create``（不含密碼）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final
from uuid import UUID

from psycopg.errors import UniqueViolation
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError, ConflictError, NotFoundError
from app.core.pagination import Page, PageParams, paginate
from app.core.permissions import resolve_effective_permissions
from app.core.security.passwords import generate_temp_password, hash_password
from app.models.account import Role, StaffUser
from app.schemas.auth import RoleBrief
from app.schemas.staff_users import (
    StaffOptionOut,
    StaffUserCreatedOut,
    StaffUserCreateIn,
    StaffUserListQuery,
    StaffUserOut,
)
from app.services import audit_service
from app.services.rbac_guards import assert_can_grant, assert_valid_permission_codes

if TYPE_CHECKING:
    from app.api.deps import CurrentStaff
    from app.core.request_meta import RequestMeta

_USERNAME_UNIQUE: Final = "uq_staff_users_username"


def staff_user_out(staff: StaffUser) -> StaffUserOut:
    role = staff.role
    return StaffUserOut(
        id=staff.id,
        username=staff.username,
        display_name=staff.display_name,
        phone=staff.phone,
        email=staff.email,
        role=RoleBrief(id=role.id, code=role.code, name=role.name),
        extra_permissions=list(staff.extra_permissions),
        revoked_permissions=list(staff.revoked_permissions),
        effective_permissions=sorted(
            resolve_effective_permissions(
                role.permissions, staff.extra_permissions, staff.revoked_permissions
            )
        ),
        is_active=staff.is_active,
        must_change_password=staff.must_change_password,
        last_login_at=staff.last_login_at,
        created_at=staff.created_at,
    )


def list_staff_users(
    session: Session, query: StaffUserListQuery, page: PageParams
) -> Page[StaffUserOut]:
    stmt = select(StaffUser)
    if query.q:
        stmt = stmt.where(
            or_(
                StaffUser.username.icontains(query.q, autoescape=True),
                StaffUser.display_name.icontains(query.q, autoescape=True),
            )
        )
    if query.role_id is not None:
        stmt = stmt.where(StaffUser.role_id == query.role_id)
    if query.is_active is not None:
        stmt = stmt.where(StaffUser.is_active.is_(query.is_active))

    rows, total = paginate(
        session, stmt.order_by(StaffUser.is_active.desc(), StaffUser.username), page
    )
    return Page(items=[staff_user_out(staff) for staff in rows], total=total)


def list_staff_options(session: Session) -> list[StaffOptionOut]:
    rows = session.execute(
        select(StaffUser.id, StaffUser.display_name)
        .where(StaffUser.is_active.is_(True))
        .order_by(StaffUser.display_name, StaffUser.username)
    ).all()
    return [
        StaffOptionOut(id=staff_id, display_name=display_name) for staff_id, display_name in rows
    ]


def get_staff_user(session: Session, staff_id: UUID) -> StaffUserOut:
    staff = session.get(StaffUser, staff_id)  # role 為 lazy='joined'
    if staff is None:
        raise NotFoundError("staff_user_not_found", "找不到員工帳號")
    return staff_user_out(staff)


def create_staff_user(
    session: Session, data: StaffUserCreateIn, *, actor: CurrentStaff, meta: RequestMeta
) -> StaffUserCreatedOut:
    role = session.get(Role, data.role_id)
    if role is None:
        raise AppError("invalid_role", "角色不存在", status=422)
    extra = assert_valid_permission_codes(data.extra_permissions)
    revoked = assert_valid_permission_codes(data.revoked_permissions)
    # 防提權：新帳號的有效權限不可超出操作者本身
    assert_can_grant(actor, resolve_effective_permissions(role.permissions, extra, revoked))

    temp_password = generate_temp_password()
    staff = StaffUser(
        username=data.username.lower(),
        password_hash=hash_password(temp_password),
        display_name=data.display_name,
        phone=data.phone,
        email=data.email,
        # 以 role_id 寫入：role=role 會經 Role.staff_users（lazy=raise）的 backref 觸發 SAWarning
        role_id=role.id,
        extra_permissions=extra,
        revoked_permissions=revoked,
        is_active=True,
        must_change_password=True,
    )
    try:
        with session.begin_nested():
            session.add(staff)
            session.flush()
    except IntegrityError as exc:
        if getattr(exc.orig, "sqlstate", None) == UniqueViolation.sqlstate and (
            _USERNAME_UNIQUE in str(exc.orig)
        ):
            raise ConflictError("username_taken", "帳號名稱已被使用") from None
        raise

    audit_service.record(
        session,
        actor=audit_service.Actor.staff(actor),
        action="staff_user.create",
        entity_type="staff_user",
        entity_id=staff.id,
        after={
            "username": staff.username,
            "display_name": staff.display_name,
            "role_code": role.code,
            "extra_permissions": extra,
            "revoked_permissions": revoked,
        },
        meta=meta,
    )
    return StaffUserCreatedOut(user=staff_user_out(staff), temp_password=temp_password)
