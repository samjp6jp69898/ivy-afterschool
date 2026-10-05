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

BACKEND-090 ``update_staff_user``（移植 ivy ``api/auth.py::update_user``）：
``assert_can_manage_staff``（目標目前有效權限 ⊆ actor）→ 自己的 role / extra / revoked 不可改（409
``cannot_modify_self_permissions``，基本資料可改）→ role / 權限碼驗證 → 新有效權限中「原本沒有的碼」
須 ⊆ actor（``assert_can_grant``）→ 權限有變時先取 ``rbac:admin_retained`` advisory lock 再
``assert_admin_capabilities_retained``（兩筆交易各降權一位管理者時序列化）→ 稽核
``staff_user.update`` 只含有變動的欄位（沒有變動不寫）。
BACKEND-091 ``reset_password``：自己 409 ``cannot_reset_self``（請用改密碼）；臨時密碼、
``must_change_password = true``、``token_version += 1``、撤銷全部 refresh；停用帳號也可重設；
稽核不含密碼。
BACKEND-092 ``deactivate``：自己 409 ``cannot_deactivate_self``；已停用冪等（不重複寫 audit）；
``is_active = false``、``token_version += 1``、撤銷全部 refresh；class_staff 不刪（歷史）；advisory
lock 後 ``assert_admin_capabilities_retained``；稽核 ``staff_user.deactivate``。重新啟用由
BACKEND-521。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final
from uuid import UUID

from psycopg.errors import UniqueViolation
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError, ConflictError, NotFoundError
from app.core.locks import advisory_xact_lock
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
    StaffUserUpdateIn,
    TempPasswordOut,
)
from app.services import audit_service
from app.services.auth import refresh_tokens
from app.services.rbac_guards import (
    assert_admin_capabilities_retained,
    assert_can_grant,
    assert_can_manage_staff,
    assert_valid_permission_codes,
)

if TYPE_CHECKING:
    from app.api.deps import CurrentStaff
    from app.core.clock import Clock
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


# --- BACKEND-090 / 091 / 092 ----------------------------------------------------------------------

_PERMISSION_FIELDS: Final = ("role_id", "extra_permissions", "revoked_permissions")
_PROFILE_FIELDS: Final = ("display_name", "phone", "email")


def _effective(staff: StaffUser) -> frozenset[str]:
    return resolve_effective_permissions(
        staff.role.permissions, staff.extra_permissions, staff.revoked_permissions
    )


def _get_managed_staff(
    session: Session, staff_id: UUID, *, actor: CurrentStaff, self_error: tuple[str, str] | None
) -> StaffUser:
    """不存在 404；``self_error`` 給值時對自己操作 → 409；目標目前的有效權限必須 ⊆ actor（403
    ``cannot_manage_staff``）。"""
    staff = session.get(StaffUser, staff_id)
    if staff is None:
        raise NotFoundError("staff_user_not_found", "找不到員工帳號")
    if self_error is not None and staff.id == actor.id:
        raise ConflictError(*self_error)
    assert_can_manage_staff(actor, _effective(staff))
    return staff


def _invalidate_logins(session: Session, staff: StaffUser, *, clock: Clock) -> None:
    """既有 access（token_version 不符）與全部 refresh family 立即失效。"""
    staff.token_version += 1
    refresh_tokens.revoke_all_for_subject(
        session, subject_type="staff", subject_id=staff.id, clock=clock
    )


def _record(
    session: Session,
    *,
    actor: CurrentStaff,
    action: str,
    staff: StaffUser,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    meta: RequestMeta,
) -> None:
    audit_service.record(
        session,
        actor=audit_service.Actor.staff(actor),
        action=action,
        entity_type="staff_user",
        entity_id=staff.id,
        before=before,
        after=after,
        meta=meta,
    )


def update_staff_user(
    session: Session,
    staff_id: UUID,
    data: StaffUserUpdateIn,
    *,
    actor: CurrentStaff,
    meta: RequestMeta,
) -> StaffUserOut:
    staff = _get_managed_staff(session, staff_id, actor=actor, self_error=None)
    changed = data.model_fields_set
    touches_permissions = any(field in changed for field in _PERMISSION_FIELDS)
    if touches_permissions and staff.id == actor.id:
        raise ConflictError("cannot_modify_self_permissions", "不可修改自己的角色或個別權限")

    role = staff.role
    if data.role_id is not None and data.role_id != staff.role_id:
        found = session.get(Role, data.role_id)
        if found is None:
            raise AppError("invalid_role", "角色不存在", status=422)
        role = found
    extra = (
        assert_valid_permission_codes(data.extra_permissions)
        if data.extra_permissions is not None
        else list(staff.extra_permissions)
    )
    revoked = (
        assert_valid_permission_codes(data.revoked_permissions)
        if data.revoked_permissions is not None
        else list(staff.revoked_permissions)
    )
    before_effective = _effective(staff)
    after_effective = resolve_effective_permissions(role.permissions, extra, revoked)
    # 只有「原本沒有的碼」算新授出；撤銷再還原原有的碼不受限
    assert_can_grant(actor, after_effective - before_effective)

    before: dict[str, Any] = {}
    after: dict[str, Any] = {}
    if role.id != staff.role_id:
        before["role_code"], after["role_code"] = staff.role.code, role.code
        staff.role_id = role.id
    if extra != list(staff.extra_permissions):
        before["extra_permissions"], after["extra_permissions"] = (
            list(staff.extra_permissions),
            extra,
        )
        staff.extra_permissions = extra
    if revoked != list(staff.revoked_permissions):
        before["revoked_permissions"] = list(staff.revoked_permissions)
        after["revoked_permissions"] = revoked
        staff.revoked_permissions = revoked
    for field in _PROFILE_FIELDS:
        if field not in changed:
            continue
        new_value = getattr(data, field)
        old_value = getattr(staff, field)
        if new_value != old_value:
            before[field], after[field] = old_value, new_value
            setattr(staff, field, new_value)
    session.flush()
    # role 為 lazy='joined'：換角色後重新載入，輸出與稽核才拿到新角色
    session.refresh(staff)

    if touches_permissions and after:
        advisory_xact_lock(session, "rbac:admin_retained", "global")
        assert_admin_capabilities_retained(session)
    if after:
        _record(
            session,
            actor=actor,
            action="staff_user.update",
            staff=staff,
            before=before,
            after=after,
            meta=meta,
        )
    return staff_user_out(staff)


def reset_password(
    session: Session, staff_id: UUID, *, actor: CurrentStaff, meta: RequestMeta, clock: Clock
) -> TempPasswordOut:
    staff = _get_managed_staff(
        session,
        staff_id,
        actor=actor,
        self_error=("cannot_reset_self", "請改用「修改密碼」變更自己的密碼"),
    )
    temp_password = generate_temp_password()
    staff.password_hash = hash_password(temp_password)
    staff.must_change_password = True
    _invalidate_logins(session, staff, clock=clock)
    session.flush()
    _record(
        session,
        actor=actor,
        action="staff_user.reset_password",
        staff=staff,
        before=None,
        after={"must_change_password": True, "token_version": staff.token_version},
        meta=meta,
    )
    return TempPasswordOut(temp_password=temp_password)


def deactivate(
    session: Session, staff_id: UUID, *, actor: CurrentStaff, meta: RequestMeta, clock: Clock
) -> StaffUserOut:
    staff = _get_managed_staff(
        session, staff_id, actor=actor, self_error=("cannot_deactivate_self", "不可停用自己的帳號")
    )
    if not staff.is_active:
        return staff_user_out(staff)  # 冪等
    staff.is_active = False
    _invalidate_logins(session, staff, clock=clock)
    session.flush()
    advisory_xact_lock(session, "rbac:admin_retained", "global")
    assert_admin_capabilities_retained(session)
    _record(
        session,
        actor=actor,
        action="staff_user.deactivate",
        staff=staff,
        before={"is_active": True},
        after={"is_active": False, "token_version": staff.token_version},
        meta=meta,
    )
    return staff_user_out(staff)


def activate(
    session: Session, staff_id: UUID, *, actor: CurrentStaff, meta: RequestMeta, clock: Clock
) -> StaffUserCreatedOut:
    """重新啟用停用的帳號：產生新臨時密碼（只在此回傳一次）並要求下次登入改密碼。

    不冪等：每次啟用都換新密碼，已啟用 → 409。token_version 不變（停用時已 +1、refresh 已全部
    撤銷）。
    啟用只增加可用帳號，不需 assert_admin_capabilities_retained。
    """
    # 先鎖列再檢查（role 為 joined 載入：只鎖 staff_users）
    session.execute(
        select(StaffUser)
        .where(StaffUser.id == staff_id)
        .with_for_update(of=StaffUser)
        .execution_options(populate_existing=True)
    )
    staff = _get_managed_staff(session, staff_id, actor=actor, self_error=None)
    if staff.is_active:
        raise ConflictError("staff_already_active", "此帳號已是啟用狀態")
    temp_password = generate_temp_password()
    staff.is_active = True
    staff.password_hash = hash_password(temp_password)
    staff.must_change_password = True
    session.flush()
    _record(
        session,
        actor=actor,
        action="staff_user.activate",
        staff=staff,
        before={"is_active": False},
        after={"is_active": True, "must_change_password": True},
        meta=meta,
    )
    return StaffUserCreatedOut(user=staff_user_out(staff), temp_password=temp_password)
