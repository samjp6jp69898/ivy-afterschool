"""BACKEND-087：app/services/staff_user_service.py（list_staff_users：分頁、搜尋、篩選）。
BACKEND-527：list_staff_options（啟用員工下拉選項，只回 id 與 display_name）。
BACKEND-088：get_staff_user（有效權限、停用可查、404）。
BACKEND-089：create_staff_user（username 小寫唯一、角色 / 權限碼驗證、防提權、臨時密碼、稽核）。"""

import json
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import CurrentStaff
from app.core.errors import AppError
from app.core.pagination import PageParams
from app.core.permissions import ALL_PERMISSIONS
from app.core.request_meta import RequestMeta
from app.core.security.passwords import verify_password
from app.models.account import Role, StaffUser
from app.models.audit import AuditLog
from app.schemas.staff_users import (
    StaffOptionOut,
    StaffUserCreatedOut,
    StaffUserCreateIn,
    StaffUserListQuery,
    StaffUserOut,
)
from app.services.staff_user_service import (
    create_staff_user,
    get_staff_user,
    list_staff_options,
    list_staff_users,
)
from tests.support.factories import make_staff

_META = RequestMeta(ip="127.0.0.1", user_agent="pytest", request_id="req-1")


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


# --- BACKEND-088：get_staff_user -----------------------------------------------------------------


def test_get_staff_user_success(db_session: Session) -> None:
    staff = make_staff(
        db_session,
        permissions=["students:read"],
        extra_permissions=["pickup:read"],
        display_name="林老師",
        is_active=False,
    )

    out = get_staff_user(db_session, staff.id)

    assert isinstance(out, StaffUserOut)
    assert out.id == staff.id
    assert out.username == staff.username
    assert out.display_name == "林老師"
    assert out.role.id == staff.role_id
    assert out.effective_permissions == ["pickup:read", "students:read"]
    assert out.extra_permissions == ["pickup:read"]
    assert out.is_active is False  # 停用帳號也可查
    assert "password" not in out.model_dump()


def test_get_staff_user_not_found(db_session: Session) -> None:
    with pytest.raises(AppError) as exc:
        get_staff_user(db_session, uuid4())

    assert (exc.value.status, exc.value.code) == (404, "staff_user_not_found")


# --- BACKEND-089：create_staff_user --------------------------------------------------------------


def _role(db: Session, code: str) -> Role:
    return db.execute(select(Role).where(Role.code == code)).scalar_one()


def _actor(permissions: frozenset[str], role_code: str = "admin") -> CurrentStaff:
    return CurrentStaff(
        id=uuid4(),
        username="boss",
        display_name="園長",
        role_id=uuid4(),
        role_code=role_code,
        role_name=role_code,
        permissions=permissions,
        must_change_password=False,
        token_version=0,
    )


def _admin() -> CurrentStaff:
    return _actor(ALL_PERMISSIONS)


def _create_audits(db: Session, staff_id: object) -> list[AuditLog]:
    return list(
        db.execute(
            select(AuditLog).where(
                AuditLog.action == "staff_user.create", AuditLog.entity_id == str(staff_id)
            )
        ).scalars()
    )


def test_create_staff_user_success(db_session: Session) -> None:
    tutor = _role(db_session, "tutor")
    actor = _admin()

    result = create_staff_user(
        db_session,
        StaffUserCreateIn(username="Wang.Tutor", display_name="王老師", role_id=tutor.id),
        actor=actor,
        meta=_META,
    )

    assert isinstance(result, StaffUserCreatedOut)
    assert result.user.username == "wang.tutor"
    assert result.user.display_name == "王老師"
    assert result.user.role.code == "tutor"
    assert result.user.must_change_password is True
    assert result.user.is_active is True
    assert result.user.effective_permissions == sorted(tutor.permissions)
    assert len(result.temp_password) >= 12
    staff = db_session.execute(select(StaffUser).where(StaffUser.id == result.user.id)).scalar_one()
    assert staff.username == "wang.tutor"
    assert staff.must_change_password is True
    assert verify_password(result.temp_password, staff.password_hash) is True
    assert verify_password("wrong-password", staff.password_hash) is False
    assert result.temp_password not in staff.password_hash


def test_create_staff_user_username_taken(db_session: Session) -> None:
    make_staff(db_session, username="wang.tutor")
    tutor = _role(db_session, "tutor")

    with pytest.raises(AppError) as exc:
        create_staff_user(
            db_session,
            StaffUserCreateIn(username="WANG.TUTOR", display_name="王老師", role_id=tutor.id),
            actor=_admin(),
            meta=_META,
        )

    assert (exc.value.status, exc.value.code) == (409, "username_taken")
    # savepoint 已 rollback：session 仍可用
    create_staff_user(
        db_session,
        StaffUserCreateIn(username="wang.tutor2", display_name="王老師", role_id=tutor.id),
        actor=_admin(),
        meta=_META,
    )


def test_create_staff_user_invalid_role_and_perm(db_session: Session) -> None:
    tutor = _role(db_session, "tutor")

    with pytest.raises(AppError) as role_exc:
        create_staff_user(
            db_session,
            StaffUserCreateIn(username="wang.tutor", display_name="王老師", role_id=uuid4()),
            actor=_admin(),
            meta=_META,
        )
    with pytest.raises(AppError) as extra_exc:
        create_staff_user(
            db_session,
            StaffUserCreateIn(
                username="wang.tutor",
                display_name="王老師",
                role_id=tutor.id,
                extra_permissions=["x:y"],
            ),
            actor=_admin(),
            meta=_META,
        )
    with pytest.raises(AppError) as revoked_exc:
        create_staff_user(
            db_session,
            StaffUserCreateIn(
                username="wang.tutor",
                display_name="王老師",
                role_id=tutor.id,
                revoked_permissions=["*"],
            ),
            actor=_admin(),
            meta=_META,
        )

    assert (role_exc.value.status, role_exc.value.code) == (422, "invalid_role")
    assert (extra_exc.value.status, extra_exc.value.code) == (422, "unknown_permission")
    assert extra_exc.value.details == {"invalid": ["x:y"]}
    assert (revoked_exc.value.status, revoked_exc.value.code) == (422, "unknown_permission")
    assert (
        db_session.execute(
            select(StaffUser).where(StaffUser.username == "wang.tutor")
        ).scalar_one_or_none()
        is None
    )


def test_create_staff_user_cannot_grant(db_session: Session) -> None:
    director = _role(db_session, "director")
    admin = _role(db_session, "admin")
    tutor = _role(db_session, "tutor")
    actor = _actor(frozenset(director.permissions) | {"staff:write"}, role_code="director")

    with pytest.raises(AppError) as by_role:
        create_staff_user(
            db_session,
            StaffUserCreateIn(username="new.admin", display_name="新管理員", role_id=admin.id),
            actor=actor,
            meta=_META,
        )
    beyond = sorted(ALL_PERMISSIONS - set(director.permissions) - {"staff:write"})
    with pytest.raises(AppError) as by_extra:
        create_staff_user(
            db_session,
            StaffUserCreateIn(
                username="new.tutor",
                display_name="新老師",
                role_id=tutor.id,
                extra_permissions=beyond[:1],
            ),
            actor=actor,
            meta=_META,
        )

    assert (by_role.value.status, by_role.value.code) == (403, "cannot_grant_permissions")
    assert by_role.value.details == {"permissions": beyond}
    assert (by_extra.value.status, by_extra.value.code) == (403, "cannot_grant_permissions")
    # 撤銷掉超出的碼後就在 actor 權限內，可以建立
    ok = create_staff_user(
        db_session,
        StaffUserCreateIn(
            username="new.tutor",
            display_name="新老師",
            role_id=tutor.id,
            extra_permissions=beyond[:1],
            revoked_permissions=beyond[:1],
        ),
        actor=actor,
        meta=_META,
    )
    assert beyond[0] not in ok.user.effective_permissions


def test_create_staff_user_audit_no_password(db_session: Session) -> None:
    tutor = _role(db_session, "tutor")
    actor = _admin()

    result = create_staff_user(
        db_session,
        StaffUserCreateIn(
            username="wang.tutor",
            display_name="王老師",
            role_id=tutor.id,
            extra_permissions=["audit:read"],
            revoked_permissions=["homework:write"],
        ),
        actor=actor,
        meta=_META,
    )

    audits = _create_audits(db_session, result.user.id)
    assert len(audits) == 1
    audit = audits[0]
    assert audit.after == {
        "username": "wang.tutor",
        "display_name": "王老師",
        "role_code": "tutor",
        "extra_permissions": ["audit:read"],
        "revoked_permissions": ["homework:write"],
    }
    dumped = json.dumps(audit.after, ensure_ascii=False)
    assert result.temp_password not in dumped
    assert "password" not in dumped
    assert (audit.actor_type, audit.actor_id, audit.entity_type) == (
        "staff",
        actor.id,
        "staff_user",
    )
    assert audit.before is None
