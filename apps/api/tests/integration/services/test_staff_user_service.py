"""BACKEND-087：app/services/staff_user_service.py（list_staff_users：分頁、搜尋、篩選）。
BACKEND-527：list_staff_options（啟用員工下拉選項，只回 id 與 display_name）。"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.pagination import PageParams
from app.models.account import Role, StaffUser
from app.schemas.staff_users import StaffOptionOut, StaffUserListQuery, StaffUserOut
from app.services.staff_user_service import list_staff_options, list_staff_users
from tests.support.factories import make_staff


def _page(page: int = 1, page_size: int = 20) -> PageParams:
    return PageParams(page=page, page_size=page_size)


def test_list_staff_users_search(db_session: Session) -> None:
    lin = make_staff(
        db_session,
        username="lin.teacher",
        role_code="tutor",
        display_name="林老師",
        extra_permissions=["audit:read"],
        revoked_permissions=["homework:write"],
    )
    make_staff(db_session, username="chen.clerk", role_code="clerk", display_name="陳行政")

    by_name = list_staff_users(db_session, StaffUserListQuery(q="林"), _page())
    by_username = list_staff_users(db_session, StaffUserListQuery(q="clerk"), _page())
    literal_percent = list_staff_users(db_session, StaffUserListQuery(q="%"), _page())
    literal_underscore = list_staff_users(db_session, StaffUserListQuery(q="_"), _page())

    assert [u.username for u in by_name.items] == ["lin.teacher"]
    assert by_name.total == 1
    assert [u.username for u in by_username.items] == ["chen.clerk"]
    assert literal_percent.total == 0
    assert literal_percent.items == []
    assert literal_underscore.total == 0
    # 輸出欄位
    out = by_name.items[0]
    assert isinstance(out, StaffUserOut)
    assert out.id == lin.id
    assert out.display_name == "林老師"
    assert out.role.code == "tutor"
    assert out.role.id == lin.role_id
    assert out.extra_permissions == ["audit:read"]
    assert out.revoked_permissions == ["homework:write"]
    assert "audit:read" in out.effective_permissions
    assert "homework:write" not in out.effective_permissions
    assert out.effective_permissions == sorted(out.effective_permissions)
    assert out.is_active is True
    assert out.must_change_password is False
    assert out.last_login_at is None
    assert "password" not in StaffUserOut.model_fields


def test_list_staff_users_filters(db_session: Session) -> None:
    tutor = db_session.execute(select(Role).where(Role.code == "tutor")).scalar_one()
    active_tutor = make_staff(db_session, role_code="tutor", display_name="林老師")
    inactive_clerk = make_staff(
        db_session, role_code="clerk", display_name="陳行政", is_active=False
    )
    make_staff(db_session, permissions=["students:read"], display_name="王老師")

    inactive = list_staff_users(db_session, StaffUserListQuery(is_active=False), _page())
    tutors = list_staff_users(db_session, StaffUserListQuery(role_id=tutor.id), _page())
    both = list_staff_users(
        db_session, StaffUserListQuery(role_id=tutor.id, is_active=False), _page()
    )

    assert inactive.total >= 1
    assert all(u.is_active is False for u in inactive.items)
    assert inactive_clerk.id in {u.id for u in inactive.items}
    assert active_tutor.id not in {u.id for u in inactive.items}
    assert tutors.total >= 1
    assert all(u.role.code == "tutor" for u in tutors.items)
    assert active_tutor.id in {u.id for u in tutors.items}
    assert inactive_clerk.id not in {u.id for u in tutors.items}
    assert active_tutor.id not in {u.id for u in both.items}


def test_list_staff_users_order_and_page(db_session: Session) -> None:
    make_staff(db_session, username="page.c", display_name="分頁老師")
    make_staff(db_session, username="page.a", display_name="分頁老師", is_active=False)
    make_staff(db_session, username="page.d", display_name="分頁老師")
    make_staff(db_session, username="page.b", display_name="分頁老師")
    query = StaffUserListQuery(q="分頁老師")

    first = list_staff_users(db_session, query, _page(page=1, page_size=2))
    second = list_staff_users(db_session, query, _page(page=2, page_size=2))
    beyond = list_staff_users(db_session, query, _page(page=3, page_size=2))

    assert first.total == second.total == beyond.total == 4
    assert [u.username for u in first.items] == ["page.b", "page.c"]
    assert all(u.is_active for u in first.items)
    # 啟用在前、同狀態依 username；停用帳號排最後
    assert [(u.username, u.is_active) for u in second.items] == [
        ("page.d", True),
        ("page.a", False),
    ]
    assert beyond.items == []


def test_list_staff_options(db_session: Session) -> None:
    make_staff(db_session, username="lin.teacher", display_name="林老師")
    make_staff(db_session, username="chen.clerk", display_name="陳行政")
    make_staff(db_session, username="wang.teacher", display_name="王老師", is_active=False)
    # 同 display_name 依 username
    make_staff(db_session, username="lin.b", display_name="林老師")
    make_staff(db_session, username="lin.a", display_name="林老師")

    options = list_staff_options(db_session)

    names = [o.display_name for o in options]
    assert "王老師" not in names
    assert names == sorted(names)
    assert names.index("林老師") < names.index("陳行政")
    lin_ids = [o.id for o in options if o.display_name == "林老師"]
    usernames = {s.id: s.username for s in db_session.execute(select(StaffUser)).scalars()}
    assert [usernames[i] for i in lin_ids] == ["lin.a", "lin.b", "lin.teacher"]
    assert all(isinstance(o, StaffOptionOut) for o in options)


def test_list_staff_options_fields(db_session: Session) -> None:
    make_staff(db_session, display_name="林老師", role_code="admin")

    options = list_staff_options(db_session)

    assert len(options) >= 1
    assert all(o.model_dump().keys() == {"id", "display_name"} for o in options)
    assert set(StaffOptionOut.model_fields) == {"id", "display_name"}
