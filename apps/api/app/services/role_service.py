"""BACKEND-077：角色服務（清單含有效權限與啟用中員工數）。"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.api.deps import CurrentStaff
from app.core.errors import ConflictError, NotFoundError
from app.core.permissions import resolve_effective_permissions
from app.core.request_meta import RequestMeta
from app.models.account import Role, StaffUser
from app.schemas.roles import RoleOut
from app.services import audit_service


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
