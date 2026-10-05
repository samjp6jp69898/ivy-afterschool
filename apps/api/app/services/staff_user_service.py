"""BACKEND-087：員工帳號服務 ``list_staff_users``（移植 ivy ``api/auth.py::list_users``；去掉
tenant、employee 連動、家長列可見性，改為分頁 + 搜尋 + 篩選）。

- q：username 或 display_name 不分大小寫模糊比對，``%`` ``_`` 以 autoescape 當字面值。
- role_id、is_active 篩選；排序 ``is_active desc, username``（啟用帳號在前）。
- effective_permissions 以 BACKEND-072 ``resolve_effective_permissions`` 計算並排序；
  ``StaffUser.role`` 為 lazy='joined'，列表不 N+1。

BACKEND-527：``list_staff_options``：啟用員工的下拉選項（班級負責老師指派用，clerk 沒有 staff:read
也要能用），只回 id 與 display_name，依 display_name、username 排序，不分頁。
"""

from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.pagination import Page, PageParams, paginate
from app.core.permissions import resolve_effective_permissions
from app.models.account import StaffUser
from app.schemas.auth import RoleBrief
from app.schemas.staff_users import StaffOptionOut, StaffUserListQuery, StaffUserOut


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
