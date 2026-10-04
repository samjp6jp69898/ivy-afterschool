"""BACKEND-077：app/services/role_service.py（list_roles）。"""

from sqlalchemy.orm import Session

from app.core.permissions import ALL_PERMISSIONS
from app.services.role_service import list_roles
from tests.support.factories import make_role, make_staff


def test_list_roles_order_and_counts(db_session: Session) -> None:
    role = make_role(db_session, code="front_desk", permissions=["students:read"])
    make_role(db_session, code="aaa_custom")
    for active in (True, True, False):
        make_staff(db_session, is_active=active).role = role
    db_session.flush()

    roles = list_roles(db_session)

    assert {r.code for r in roles[:4]} == {"admin", "director", "clerk", "tutor"}
    assert all(r.is_system for r in roles[:4])
    custom = [r.code for r in roles[4:]]
    assert custom == sorted(custom)
    by_code = {r.code: r for r in roles}
    assert by_code["front_desk"].staff_count == 2
    assert by_code["aaa_custom"].staff_count == 0
    assert by_code["front_desk"].effective_permissions == ["students:read"]
    assert [r.code for r in roles[:4]] == sorted(r.code for r in roles[:4])


def test_list_roles_admin_effective(db_session: Session) -> None:
    admin = next(r for r in list_roles(db_session) if r.code == "admin")

    assert admin.permissions == ["*"]
    assert len(admin.effective_permissions) == 28
    assert admin.effective_permissions == sorted(ALL_PERMISSIONS)
