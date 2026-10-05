"""BACKEND-087：app/services/staff_user_service.py（list_staff_users：分頁、搜尋、篩選）。
BACKEND-527：list_staff_options（啟用員工下拉選項，只回 id 與 display_name）。
BACKEND-088：get_staff_user（有效權限、停用可查、404）。
BACKEND-089：create_staff_user（username 小寫唯一、角色 / 權限碼驗證、防提權、臨時密碼、稽核）。
BACKEND-090：update_staff_user（基本資料 / 角色 / 個別權限、cannot_manage、自改權限 409、防提權、
最後一位管理者 409）。
BACKEND-091：reset_password（臨時密碼、token_version +1、撤銷 refresh、自己 409、稽核不含密碼）。
BACKEND-092：deactivate（停用、token 失效、冪等、最後一位管理者 409、兩 session 並發各停一位
admin）。
BACKEND-521：activate（重新啟用、臨時密碼只回一次、強制改密碼、token_version 不變、稽核不含
密碼）。"""

import json
import threading
from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, func, select, update
from sqlalchemy.orm import Session

from app.api.deps import CurrentStaff
from app.core.errors import AppError
from app.core.pagination import PageParams
from app.core.permissions import ALL_PERMISSIONS, resolve_effective_permissions
from app.core.request_meta import RequestMeta
from app.core.security.passwords import verify_password
from app.models.account import RefreshToken, Role, StaffUser
from app.models.audit import AuditLog
from app.schemas.staff_users import (
    StaffOptionOut,
    StaffUserCreatedOut,
    StaffUserCreateIn,
    StaffUserListQuery,
    StaffUserOut,
    StaffUserUpdateIn,
    TempPasswordOut,
)
from app.services.auth.refresh_tokens import issue
from app.services.staff_user_service import (
    activate,
    create_staff_user,
    deactivate,
    get_staff_user,
    list_staff_options,
    list_staff_users,
    reset_password,
    update_staff_user,
)
from tests.integration.db.conftest import connect_owner
from tests.support.factories import make_staff
from tests.support.fake_clock import FakeClock

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


# --- BACKEND-090：update_staff_user ---------------------------------------------------------------


def _audits(db: Session, action: str, staff_id: object) -> list[AuditLog]:
    return list(
        db.execute(
            select(AuditLog).where(AuditLog.action == action, AuditLog.entity_id == str(staff_id))
        ).scalars()
    )


def _deactivate_everyone(db: Session) -> None:
    db.execute(update(StaffUser).values(is_active=False))


def _director_plus_staff_write(db: Session) -> CurrentStaff:
    return _actor(
        frozenset(_role(db, "director").permissions) | {"staff:write"}, role_code="director"
    )


def _as_staff(staff: StaffUser) -> CurrentStaff:
    role = staff.role
    return CurrentStaff(
        id=staff.id,
        username=staff.username,
        display_name=staff.display_name,
        role_id=role.id,
        role_code=role.code,
        role_name=role.name,
        permissions=resolve_effective_permissions(
            role.permissions, staff.extra_permissions, staff.revoked_permissions
        ),
        must_change_password=False,
        token_version=staff.token_version,
    )


def test_update_staff_user_success(db_session: Session) -> None:
    make_staff(db_session, role_code="admin")  # 守衛需要系統內仍有啟用中的管理者
    clerk = _role(db_session, "clerk")
    target = make_staff(db_session, role_code="tutor", display_name="王老師")
    actor = _admin()

    out = update_staff_user(
        db_session,
        target.id,
        StaffUserUpdateIn(role_id=clerk.id, display_name="王行政", phone="0912-000-123"),
        actor=actor,
        meta=_META,
    )

    assert out.role.code == "clerk"
    assert out.display_name == "王行政"
    assert out.phone == "0912-000-123"
    assert out.effective_permissions == sorted(clerk.permissions)
    db_session.refresh(target)
    assert (target.role_id, target.display_name, target.phone) == (
        clerk.id,
        "王行政",
        "0912-000-123",
    )
    audits = _audits(db_session, "staff_user.update", target.id)
    assert len(audits) == 1
    assert audits[0].before == {"role_code": "tutor", "display_name": "王老師", "phone": None}
    assert audits[0].after == {
        "role_code": "clerk",
        "display_name": "王行政",
        "phone": "0912-000-123",
    }
    assert audits[0].actor_id == actor.id
    # 個別權限
    out2 = update_staff_user(
        db_session,
        target.id,
        StaffUserUpdateIn(extra_permissions=["audit:read"], revoked_permissions=["students:write"]),
        actor=actor,
        meta=_META,
    )
    assert "audit:read" in out2.effective_permissions
    assert "students:write" not in out2.effective_permissions
    second = _audits(db_session, "staff_user.update", target.id)[1]
    assert second.before == {"extra_permissions": [], "revoked_permissions": []}
    assert second.after == {
        "extra_permissions": ["audit:read"],
        "revoked_permissions": ["students:write"],
    }
    # 沒有實際變動：不寫 audit
    update_staff_user(
        db_session, target.id, StaffUserUpdateIn(display_name="王行政"), actor=actor, meta=_META
    )
    assert len(_audits(db_session, "staff_user.update", target.id)) == 2
    with pytest.raises(AppError) as missing:
        update_staff_user(
            db_session, uuid4(), StaffUserUpdateIn(display_name="x"), actor=actor, meta=_META
        )
    assert (missing.value.status, missing.value.code) == (404, "staff_user_not_found")
    with pytest.raises(AppError) as bad_role:
        update_staff_user(
            db_session, target.id, StaffUserUpdateIn(role_id=uuid4()), actor=actor, meta=_META
        )
    assert (bad_role.value.status, bad_role.value.code) == (422, "invalid_role")


def test_update_staff_user_cannot_manage(db_session: Session) -> None:
    admin_account = make_staff(db_session, role_code="admin", display_name="園長")
    actor = _director_plus_staff_write(db_session)

    with pytest.raises(AppError) as exc:
        update_staff_user(
            db_session,
            admin_account.id,
            StaffUserUpdateIn(display_name="改名"),
            actor=actor,
            meta=_META,
        )

    assert (exc.value.status, exc.value.code) == (403, "cannot_manage_staff")
    db_session.refresh(admin_account)
    assert admin_account.display_name == "園長"


def test_update_staff_user_self_permissions(db_session: Session) -> None:
    me = make_staff(db_session, permissions=["staff:write", "students:read"])
    actor = _as_staff(me)

    with pytest.raises(AppError) as extra:
        update_staff_user(
            db_session,
            me.id,
            StaffUserUpdateIn(extra_permissions=["students:read"]),
            actor=actor,
            meta=_META,
        )
    with pytest.raises(AppError) as role:
        update_staff_user(
            db_session, me.id, StaffUserUpdateIn(role_id=me.role_id), actor=actor, meta=_META
        )

    assert (extra.value.status, extra.value.code) == (409, "cannot_modify_self_permissions")
    assert (role.value.status, role.value.code) == (409, "cannot_modify_self_permissions")
    out = update_staff_user(
        db_session, me.id, StaffUserUpdateIn(phone="0912-000-999"), actor=actor, meta=_META
    )
    assert out.phone == "0912-000-999"


def test_update_staff_user_cannot_grant(db_session: Session) -> None:
    tutor = _role(db_session, "tutor")
    assert "pickup:override" not in tutor.permissions
    make_staff(db_session, role_code="admin")
    target = make_staff(db_session, role_code="tutor")
    actor = _actor(frozenset(tutor.permissions) | {"staff:write"}, role_code="tutor")

    with pytest.raises(AppError) as exc:
        update_staff_user(
            db_session,
            target.id,
            StaffUserUpdateIn(extra_permissions=["pickup:override"]),
            actor=actor,
            meta=_META,
        )

    assert (exc.value.status, exc.value.code) == (403, "cannot_grant_permissions")
    assert exc.value.details == {"permissions": ["pickup:override"]}
    # 原本就有的碼不算新授出：撤銷再還原不受限
    update_staff_user(
        db_session,
        target.id,
        StaffUserUpdateIn(revoked_permissions=["homework:write"]),
        actor=actor,
        meta=_META,
    )
    out = update_staff_user(
        db_session, target.id, StaffUserUpdateIn(revoked_permissions=[]), actor=actor, meta=_META
    )
    assert "homework:write" in out.effective_permissions


def test_update_staff_user_last_manager(db_session: Session) -> None:
    _deactivate_everyone(db_session)
    tutor = _role(db_session, "tutor")
    mgr = make_staff(db_session, permissions=["roles:write", "staff:write"])
    make_staff(db_session, role_code="admin", is_active=False)
    actor = _admin()

    with pytest.raises(AppError) as exc:
        update_staff_user(
            db_session, mgr.id, StaffUserUpdateIn(role_id=tutor.id), actor=actor, meta=_META
        )

    assert (exc.value.status, exc.value.code) == (409, "last_role_manager")


# --- BACKEND-091：reset_password ------------------------------------------------------------------


def _refresh_rows(db: Session, staff_id: UUID) -> list[RefreshToken]:
    return list(
        db.execute(
            select(RefreshToken).where(
                RefreshToken.subject_type == "staff", RefreshToken.subject_id == staff_id
            )
        ).scalars()
    )


def test_reset_password_success(db_session: Session, fake_clock: FakeClock) -> None:
    target = make_staff(db_session, role_code="tutor")
    issue(db_session, subject_type="staff", subject_id=target.id, clock=fake_clock)

    out = reset_password(db_session, target.id, actor=_admin(), meta=_META, clock=fake_clock)

    assert isinstance(out, TempPasswordOut)
    assert len(out.temp_password) >= 12
    db_session.refresh(target)
    assert verify_password(out.temp_password, target.password_hash) is True
    assert verify_password("Passw0rd-Test1", target.password_hash) is False
    assert target.token_version == 1
    assert target.must_change_password is True
    rows = _refresh_rows(db_session, target.id)
    assert len(rows) == 1
    assert all(r.revoked_at is not None for r in rows)
    # 停用帳號也可重設
    inactive = make_staff(db_session, role_code="tutor", is_active=False)
    assert reset_password(db_session, inactive.id, actor=_admin(), meta=_META, clock=fake_clock)
    with pytest.raises(AppError) as missing:
        reset_password(db_session, uuid4(), actor=_admin(), meta=_META, clock=fake_clock)
    assert (missing.value.status, missing.value.code) == (404, "staff_user_not_found")


def test_reset_password_self(db_session: Session, fake_clock: FakeClock) -> None:
    me = make_staff(db_session, role_code="admin")

    with pytest.raises(AppError) as exc:
        reset_password(db_session, me.id, actor=_as_staff(me), meta=_META, clock=fake_clock)

    assert (exc.value.status, exc.value.code) == (409, "cannot_reset_self")
    db_session.refresh(me)
    assert me.token_version == 0


def test_reset_password_cannot_manage(db_session: Session, fake_clock: FakeClock) -> None:
    admin_account = make_staff(db_session, role_code="admin")

    with pytest.raises(AppError) as exc:
        reset_password(
            db_session,
            admin_account.id,
            actor=_director_plus_staff_write(db_session),
            meta=_META,
            clock=fake_clock,
        )

    assert (exc.value.status, exc.value.code) == (403, "cannot_manage_staff")
    db_session.refresh(admin_account)
    assert admin_account.token_version == 0


def test_reset_password_audit(db_session: Session, fake_clock: FakeClock) -> None:
    target = make_staff(db_session, role_code="tutor")
    actor = _admin()

    out = reset_password(db_session, target.id, actor=actor, meta=_META, clock=fake_clock)

    audits = _audits(db_session, "staff_user.reset_password", target.id)
    assert len(audits) == 1
    dumped = json.dumps([audits[0].before, audits[0].after], ensure_ascii=False)
    assert out.temp_password not in dumped
    assert "password" not in dumped.lower() or "password_hash" not in dumped
    assert (audits[0].actor_type, audits[0].actor_id, audits[0].entity_type) == (
        "staff",
        actor.id,
        "staff_user",
    )


# --- BACKEND-092：deactivate ----------------------------------------------------------------------


def test_deactivate_success(db_session: Session, fake_clock: FakeClock) -> None:
    make_staff(db_session, role_code="admin")  # 守衛需要系統內仍有啟用中的管理者
    target = make_staff(db_session, role_code="tutor")
    issue(db_session, subject_type="staff", subject_id=target.id, clock=fake_clock)
    issue(db_session, subject_type="staff", subject_id=target.id, clock=fake_clock)

    out = deactivate(db_session, target.id, actor=_admin(), meta=_META, clock=fake_clock)

    assert out.is_active is False
    db_session.refresh(target)
    assert target.is_active is False
    assert target.token_version == 1
    rows = _refresh_rows(db_session, target.id)
    assert len(rows) == 2
    assert all(r.revoked_at is not None for r in rows)
    assert len(_audits(db_session, "staff_user.deactivate", target.id)) == 1
    with pytest.raises(AppError) as missing:
        deactivate(db_session, uuid4(), actor=_admin(), meta=_META, clock=fake_clock)
    assert (missing.value.status, missing.value.code) == (404, "staff_user_not_found")


def test_deactivate_self(db_session: Session, fake_clock: FakeClock) -> None:
    me = make_staff(db_session, role_code="admin")

    with pytest.raises(AppError) as exc:
        deactivate(db_session, me.id, actor=_as_staff(me), meta=_META, clock=fake_clock)

    assert (exc.value.status, exc.value.code) == (409, "cannot_deactivate_self")
    db_session.refresh(me)
    assert me.is_active is True


def test_deactivate_idempotent(db_session: Session, fake_clock: FakeClock) -> None:
    make_staff(db_session, role_code="admin")
    target = make_staff(db_session, role_code="tutor")
    deactivate(db_session, target.id, actor=_admin(), meta=_META, clock=fake_clock)

    again = deactivate(db_session, target.id, actor=_admin(), meta=_META, clock=fake_clock)

    assert again.is_active is False
    db_session.refresh(target)
    assert target.token_version == 1  # 不重複 +1
    assert len(_audits(db_session, "staff_user.deactivate", target.id)) == 1


def test_deactivate_last_manager(db_session: Session, fake_clock: FakeClock) -> None:
    _deactivate_everyone(db_session)
    mgr = make_staff(db_session, permissions=["roles:write", "staff:write"])
    make_staff(db_session, role_code="admin", is_active=False)

    with pytest.raises(AppError) as exc:
        deactivate(db_session, mgr.id, actor=_admin(), meta=_META, clock=fake_clock)

    assert (exc.value.status, exc.value.code) == (409, "last_role_manager")
    admin_account = make_staff(db_session, role_code="admin")
    with pytest.raises(AppError) as forbidden:
        deactivate(
            db_session,
            admin_account.id,
            actor=_director_plus_staff_write(db_session),
            meta=_META,
            clock=fake_clock,
        )
    assert (forbidden.value.status, forbidden.value.code) == (403, "cannot_manage_staff")


@pytest.fixture
def owner_cleanup_staff() -> Iterator[list[UUID]]:
    """committing 測試建立的員工（含其自訂角色、稽核列、refresh token）以 owner 連線刪除；排在
    committing_db_session 之前。"""
    staff_ids: list[UUID] = []
    yield staff_ids
    with connect_owner() as conn:
        conn.execute("set lock_timeout = '5s'")
        for staff_id in staff_ids:
            conn.execute(
                "delete from public.audit_logs where entity_type = 'staff_user' and entity_id = %s",
                (str(staff_id),),
            )
            conn.execute(
                "delete from public.refresh_tokens "
                "where subject_type = 'staff' and subject_id = %s",
                (staff_id,),
            )
            role_id = conn.execute(
                "delete from public.staff_users where id = %s returning role_id", (staff_id,)
            ).fetchone()
            if role_id is not None:
                conn.execute(
                    "delete from public.roles where id = %s and code like 'test_%%'", (role_id[0],)
                )
        conn.commit()


@pytest.mark.cleanup_tables("class_staff")
def test_deactivate_concurrent_last_admins(
    owner_cleanup_staff: list[UUID],
    committing_db_session: Session,
    db_engine: Engine,
    fake_clock: FakeClock,
) -> None:
    """兩位啟用中的 admin A、B，兩條 session 同時各停用一位：advisory lock 讓第二筆等第一筆 commit
    後才檢查 → 恰一筆成功、另一筆 409，commit 後仍至少一位啟用中的 admin。"""
    other_active = list(
        committing_db_session.execute(
            select(StaffUser.id).where(StaffUser.is_active.is_(True))
        ).scalars()
    )
    assert other_active == [], f"共用 DB 內有其他啟用員工，無法驗證最後管理者守衛：{other_active}"
    admin_a = make_staff(committing_db_session, role_code="admin", display_name="A")
    admin_b = make_staff(committing_db_session, role_code="admin", display_name="B")
    committing_db_session.commit()
    owner_cleanup_staff.extend([admin_a.id, admin_b.id])
    a_id, b_id = admin_a.id, admin_b.id
    a_locked = threading.Event()
    release_a = threading.Event()
    b_done = threading.Event()
    outcome: dict[str, object] = {}

    def worker_a() -> None:
        sa = Session(bind=db_engine)
        try:
            deactivate(sa, a_id, actor=_admin(), meta=_META, clock=fake_clock)
            a_locked.set()
            release_a.wait(timeout=10)
            sa.commit()
            outcome["a"] = "ok"
        except BaseException as exc:
            outcome["a"] = exc
            a_locked.set()
        finally:
            sa.close()

    def worker_b() -> None:
        sb = Session(bind=db_engine)
        try:
            a_locked.wait(timeout=10)
            try:
                deactivate(sb, b_id, actor=_admin(), meta=_META, clock=fake_clock)
                sb.commit()
                outcome["b"] = "ok"
            except AppError as exc:
                sb.rollback()
                outcome["b"] = (exc.status, exc.code)
        except BaseException as exc:
            outcome["b"] = exc
        finally:
            sb.close()
            b_done.set()

    threads = [threading.Thread(target=worker_a), threading.Thread(target=worker_b)]
    for t in threads:
        t.start()
    try:
        assert a_locked.wait(timeout=10)
        assert not b_done.wait(timeout=0.5), outcome  # A 持 advisory lock 未 commit：B 被擋住
    finally:
        release_a.set()
        for t in threads:
            t.join(timeout=10)

    assert outcome["a"] == "ok"
    assert outcome["b"] in {(409, "last_role_manager"), (409, "last_staff_manager")}
    with Session(bind=db_engine) as check:
        active = check.execute(
            select(func.count())
            .select_from(StaffUser)
            .where(StaffUser.id.in_([a_id, b_id]), StaffUser.is_active.is_(True))
        ).scalar_one()
        assert active == 1
        assert check.get(StaffUser, b_id).is_active is True  # type: ignore[union-attr]


# --- BACKEND-521 activate ---


def _deactivated(db: Session, role_code: str = "tutor") -> StaffUser:
    """停用中的帳號（停用時 token_version 已 +1）。"""
    staff = make_staff(db, role_code=role_code, is_active=False)
    staff.token_version = 1
    db.flush()
    return staff


def test_activate_success(db_session: Session, fake_clock: FakeClock) -> None:
    target = _deactivated(db_session)

    out = activate(db_session, target.id, actor=_admin(), meta=_META, clock=fake_clock)

    assert isinstance(out, StaffUserCreatedOut)
    assert (out.user.id, out.user.is_active, out.user.must_change_password) == (
        target.id,
        True,
        True,
    )
    db_session.refresh(target)
    assert verify_password(out.temp_password, target.password_hash) is True
    assert verify_password("Passw0rd-Test1", target.password_hash) is False
    assert (target.is_active, target.must_change_password, target.token_version) == (True, True, 1)


def test_activate_already_active(db_session: Session, fake_clock: FakeClock) -> None:
    target = make_staff(db_session, role_code="tutor")

    with pytest.raises(AppError) as exc:
        activate(db_session, target.id, actor=_admin(), meta=_META, clock=fake_clock)

    assert (exc.value.status, exc.value.code) == (409, "staff_already_active")
    assert _audits(db_session, "staff_user.activate", target.id) == []


def test_activate_cannot_manage(db_session: Session, fake_clock: FakeClock) -> None:
    admin = _deactivated(db_session, role_code="admin")
    actor = _director_plus_staff_write(db_session)

    with pytest.raises(AppError) as forbidden:
        activate(db_session, admin.id, actor=actor, meta=_META, clock=fake_clock)
    with pytest.raises(AppError) as missing:
        activate(db_session, uuid4(), actor=actor, meta=_META, clock=fake_clock)

    assert (forbidden.value.status, forbidden.value.code) == (403, "cannot_manage_staff")
    assert (missing.value.status, missing.value.code) == (404, "staff_user_not_found")
    db_session.refresh(admin)
    assert admin.is_active is False


def test_activate_audit_no_password(db_session: Session, fake_clock: FakeClock) -> None:
    target = _deactivated(db_session)
    old_hash = target.password_hash

    out = activate(db_session, target.id, actor=_admin(), meta=_META, clock=fake_clock)

    [log] = _audits(db_session, "staff_user.activate", target.id)
    assert (log.entity_type, log.ip) == ("staff_user", "127.0.0.1")
    assert log.before == {"is_active": False}
    assert log.after == {"is_active": True, "must_change_password": True}
    dumped = json.dumps([log.before, log.after])
    db_session.refresh(target)
    for secret in (out.temp_password, old_hash, target.password_hash):
        assert secret not in dumped
