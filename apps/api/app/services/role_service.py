"""BACKEND-077：角色服務（清單含有效權限與啟用中員工數）。
BACKEND-080：delete_role。
BACKEND-078：create_role（移植 ivy ``api/permissions_admin.py::create_role``；去掉 tenant 與
platform-only 碼檢查）：``assert_valid_permission_codes``（422）→ ``assert_can_grant``（403）→
code 重複 409 ``role_code_taken``（先查再插，insert 在 savepoint 內攔截 unique violation 防競態）→
``is_system = false`` → 稽核 ``role.create``。
"""

from __future__ import annotations

from uuid import UUID

from psycopg.errors import UniqueViolation
from sqlalchemy import case, exists, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import CurrentStaff
from app.core.errors import ConflictError, NotFoundError
from app.core.permissions import resolve_effective_permissions
from app.core.request_meta import RequestMeta
from app.models.account import Role, StaffUser
from app.schemas.roles import RoleCreateIn, RoleOut
from app.services import audit_service
from app.services.rbac_guards import assert_can_grant, assert_valid_permission_codes


def list_roles(session: Session) -> list[RoleOut]:
    """系統角色在前，其後依 code；staff_count 只計啟用帳號（單一 group by 查詢）。"""
    active_count = func.coalesce(func.sum(case((StaffUser.is_active.is_(True), 1), else_=0)), 0)
    rows = session.execute(
        select(Role, active_count)
        .outerjoin(StaffUser, StaffUser.role_id == Role.id)
        .group_by(Role.id)
        .order_by(Role.is_system.desc(), Role.code)
    ).all()
    return [
        RoleOut(
            id=role.id,
            code=role.code,
            name=role.name,
            description=role.description,
            is_system=role.is_system,
            permissions=list(role.permissions),
            effective_permissions=sorted(resolve_effective_permissions(role.permissions, [], [])),
            staff_count=int(count),
            created_at=role.created_at,
            updated_at=role.updated_at,
        )
        for role, count in rows
    ]


def delete_role(session: Session, role_id: UUID, *, actor: CurrentStaff, meta: RequestMeta) -> None:
    """刪除自訂角色；系統角色與仍有員工（含停用）使用的角色不可刪。

    DB FK 為 on delete restrict，這裡先查再刪以回傳友善錯誤；鎖住角色列避免與同時指派員工競爭。
    """
    role = session.execute(
        select(Role).where(Role.id == role_id).with_for_update()
    ).scalar_one_or_none()
    if role is None:
        raise NotFoundError("role_not_found", "找不到角色")
    if role.is_system:
        raise ConflictError("system_role_protected", "系統角色不可刪除")
    staff_count = session.execute(
        select(func.count()).select_from(StaffUser).where(StaffUser.role_id == role.id)
    ).scalar_one()
    if staff_count:
        raise ConflictError(
            "role_in_use", "仍有員工使用此角色，無法刪除", details={"staff_count": staff_count}
        )

    before = {"code": role.code, "name": role.name, "permissions": list(role.permissions)}
    session.delete(role)
    session.flush()
    audit_service.record(
        session,
        actor=audit_service.Actor.staff(actor),
        action="role.delete",
        entity_type="role",
        entity_id=role_id,
        before=before,
        meta=meta,
    )


def _role_code_exists(session: Session, code: str) -> bool:
    return bool(session.execute(select(exists().where(Role.code == code))).scalar_one())


def _is_role_code_collision(exc: IntegrityError) -> bool:
    if getattr(exc.orig, "sqlstate", None) != UniqueViolation.sqlstate:
        return False
    constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None) or ""
    return "roles" in constraint and "code" in constraint


def _role_code_taken(code: str) -> ConflictError:
    return ConflictError("role_code_taken", "角色代碼已存在", details={"code": code})


def create_role(
    session: Session, data: RoleCreateIn, *, actor: CurrentStaff, meta: RequestMeta
) -> RoleOut:
    codes = assert_valid_permission_codes(data.permissions)
    assert_can_grant(actor, codes)
    if _role_code_exists(session, data.code):
        raise _role_code_taken(data.code)

    role = Role(
        code=data.code,
        name=data.name,
        description=data.description,
        permissions=codes,
        is_system=False,
    )
    try:
        with session.begin_nested():
            session.add(role)
            session.flush()
    except IntegrityError as exc:
        # 先查再插之間被別人建立同 code：savepoint 回滾後 session 仍可用
        if _is_role_code_collision(exc):
            raise _role_code_taken(data.code) from None
        raise

    audit_service.record(
        session,
        actor=audit_service.Actor.staff(actor),
        action="role.create",
        entity_type="role",
        entity_id=role.id,
        after={"code": role.code, "name": role.name, "permissions": list(role.permissions)},
        meta=meta,
    )
    return RoleOut(
        id=role.id,
        code=role.code,
        name=role.name,
        description=role.description,
        is_system=role.is_system,
        permissions=list(role.permissions),
        effective_permissions=sorted(resolve_effective_permissions(role.permissions, [], [])),
        staff_count=0,
        created_at=role.created_at,
        updated_at=role.updated_at,
    )
