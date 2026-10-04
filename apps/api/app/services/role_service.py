"""BACKEND-077：角色服務（清單含有效權限與啟用中員工數）。"""

from __future__ import annotations

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.core.permissions import resolve_effective_permissions
from app.models.account import Role, StaffUser
from app.schemas.roles import RoleOut


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
