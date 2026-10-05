"""BACKEND-077：app/services/role_service.py（list_roles）。
BACKEND-080：delete_role。
BACKEND-078：create_role（權限驗證、防提權、code 重複 409 含競態、稽核）。
BACKEND-079：update_role（admin 不可改、防提權、保留管理者、稽核只含變動欄位）。"""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.api.deps import CurrentStaff
from app.core.errors import AppError
from app.core.permissions import ALL_PERMISSIONS
from app.core.request_meta import RequestMeta
from app.models.account import Role, StaffUser
from app.models.audit import AuditLog
from app.schemas.roles import RoleCreateIn, RoleOut, RoleUpdateIn
from app.services import role_service
from app.services.role_service import create_role, delete_role, list_roles, update_role
from tests.support.factories import make_role, make_staff

_META = RequestMeta(ip="203.0.113.5", user_agent="UA", request_id="r1")


def _actor(staff_id: UUID) -> CurrentStaff:
    return CurrentStaff(
        id=staff_id,
        username="admin",
        display_name="管理員",
        role_id=uuid4(),
        role_code="admin",
        role_name="管理員",
        permissions=frozenset(ALL_PERMISSIONS),
        must_change_password=False,
        token_version=0,
    )


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


def test_delete_role_success(db_session: Session) -> None:
    admin = make_staff(db_session)
    role = make_role(db_session, code="temp_role", name="臨時角色", permissions=["students:read"])
    role_id = role.id

    delete_role(db_session, role_id, actor=_actor(admin.id), meta=_META)

    assert db_session.execute(select(Role).where(Role.id == role_id)).first() is None
    log = db_session.execute(
        select(AuditLog).where(AuditLog.action == "role.delete", AuditLog.entity_id == str(role_id))
    ).scalar_one()
    assert log.actor_type == "staff"
    assert log.actor_id == admin.id
    assert log.entity_type == "role"
    assert log.before == {"code": "temp_role", "name": "臨時角色", "permissions": ["students:read"]}
    assert log.after is None
    assert log.ip == "203.0.113.5"


def test_delete_role_system(db_session: Session) -> None:
    admin = make_staff(db_session)
    tutor = db_session.execute(select(Role).where(Role.code == "tutor")).scalar_one()

    with pytest.raises(AppError) as exc:
        delete_role(db_session, tutor.id, actor=_actor(admin.id), meta=_META)

    assert (exc.value.status, exc.value.code) == (409, "system_role_protected")
    assert db_session.execute(select(Role).where(Role.id == tutor.id)).scalar_one() is tutor
    assert (
        db_session.execute(select(AuditLog).where(AuditLog.action == "role.delete")).first() is None
    )


def test_delete_role_in_use(db_session: Session) -> None:
    admin = make_staff(db_session)
    role = make_role(db_session, code="in_use_role")
    make_staff(db_session, is_active=False).role = role
    db_session.flush()
    role_id = role.id

    with pytest.raises(AppError) as exc:
        delete_role(db_session, role_id, actor=_actor(admin.id), meta=_META)

    assert (exc.value.status, exc.value.code) == (409, "role_in_use")
    assert exc.value.details == {"staff_count": 1}
    assert db_session.execute(select(Role).where(Role.id == role_id)).first() is not None
    assert (
        db_session.execute(select(AuditLog).where(AuditLog.action == "role.delete")).first() is None
    )


def test_delete_role_not_found(db_session: Session) -> None:
    admin = make_staff(db_session)

    with pytest.raises(AppError) as exc:
        delete_role(db_session, uuid4(), actor=_actor(admin.id), meta=_META)

    assert (exc.value.status, exc.value.code) == (404, "role_not_found")


# --- BACKEND-078：create_role ------------------------------------------------------------------


def _limited_actor(staff_id: UUID, *permissions: str) -> CurrentStaff:
    return CurrentStaff(
        id=staff_id,
        username="director",
        display_name="主任",
        role_id=uuid4(),
        role_code="director",
        role_name="主任",
        permissions=frozenset(permissions),
        must_change_password=False,
        token_version=0,
    )


def _create_logs(db_session: Session) -> list[AuditLog]:
    return list(
        db_session.execute(select(AuditLog).where(AuditLog.action == "role.create")).scalars()
    )


def test_create_role_success(db_session: Session) -> None:
    admin = make_staff(db_session)
    data = RoleCreateIn(
        code="front_desk", name="櫃台", permissions=["pickup:read", "pickup:operate", "pickup:read"]
    )

    out = create_role(db_session, data, actor=_actor(admin.id), meta=_META)

    assert isinstance(out, RoleOut)
    assert out.code == "front_desk"
    assert out.name == "櫃台"
    assert out.description is None
    assert out.permissions == ["pickup:operate", "pickup:read"]
    assert out.effective_permissions == ["pickup:operate", "pickup:read"]
    assert out.is_system is False
    assert out.staff_count == 0
    role = db_session.execute(select(Role).where(Role.code == "front_desk")).scalar_one()
    assert role.id == out.id
    assert role.is_system is False
    assert role.permissions == ["pickup:operate", "pickup:read"]
    logs = _create_logs(db_session)
    assert len(logs) == 1
    assert logs[0].entity_type == "role"
    assert logs[0].entity_id == str(role.id)
    assert logs[0].actor_id == admin.id
    assert logs[0].before is None
    assert logs[0].after == {
        "code": "front_desk",
        "name": "櫃台",
        "permissions": ["pickup:operate", "pickup:read"],
    }
    assert logs[0].ip == "203.0.113.5"
    # 空權限的角色也允許（之後再授權）
    empty = create_role(
        db_session,
        RoleCreateIn(code="empty_role", name="空", permissions=[]),
        actor=_actor(admin.id),
        meta=_META,
    )
    assert empty.permissions == []


def test_create_role_unknown_permission(db_session: Session) -> None:
    admin = make_staff(db_session)
    for permissions, invalid in ((["pickup:fly"], ["pickup:fly"]), (["*"], ["*"])):
        with pytest.raises(AppError) as exc:
            create_role(
                db_session,
                RoleCreateIn(code="bad_role", name="壞", permissions=permissions),
                actor=_actor(admin.id),
                meta=_META,
            )
        assert (exc.value.status, exc.value.code) == (422, "unknown_permission")
        assert exc.value.details == {"invalid": invalid}
    assert db_session.execute(select(Role).where(Role.code == "bad_role")).first() is None
    assert _create_logs(db_session) == []


def test_create_role_cannot_grant(db_session: Session) -> None:
    director = make_staff(db_session)
    actor = _limited_actor(director.id, "roles:write", "pickup:read")

    with pytest.raises(AppError) as exc:
        create_role(
            db_session,
            RoleCreateIn(code="pickup_boss", name="接送主管", permissions=["pickup:override"]),
            actor=actor,
            meta=_META,
        )

    assert (exc.value.status, exc.value.code) == (403, "cannot_grant_permissions")
    assert exc.value.details == {"permissions": ["pickup:override"]}
    assert db_session.execute(select(Role).where(Role.code == "pickup_boss")).first() is None
    # 子集合可以
    out = create_role(
        db_session,
        RoleCreateIn(code="pickup_viewer", name="接送檢視", permissions=["pickup:read"]),
        actor=actor,
        meta=_META,
    )
    assert out.permissions == ["pickup:read"]


def test_create_role_code_taken(db_session: Session) -> None:
    admin = make_staff(db_session)

    with pytest.raises(AppError) as exc:
        create_role(
            db_session,
            RoleCreateIn(code="tutor", name="另一個課輔", permissions=[]),
            actor=_actor(admin.id),
            meta=_META,
        )

    assert (exc.value.status, exc.value.code) == (409, "role_code_taken")
    assert _create_logs(db_session) == []
    # 既有 tutor 不受影響
    tutor = db_session.execute(select(Role).where(Role.code == "tutor")).scalar_one()
    assert tutor.name == "課輔老師"


def test_create_role_code_taken_race(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """先查沒查到（競態）→ insert 撞 unique → savepoint 回滾後仍回 409，session 可繼續用。"""
    admin = make_staff(db_session)
    monkeypatch.setattr(role_service, "_role_code_exists", lambda session, code: False)

    with pytest.raises(AppError) as exc:
        create_role(
            db_session,
            RoleCreateIn(code="tutor", name="另一個課輔", permissions=[]),
            actor=_actor(admin.id),
            meta=_META,
        )

    assert (exc.value.status, exc.value.code) == (409, "role_code_taken")
    assert db_session.execute(select(Role).where(Role.code == "tutor")).scalar_one().name == (
        "課輔老師"
    )
    assert _create_logs(db_session) == []


# --- BACKEND-079：update_role ------------------------------------------------------------------


def _update_logs(db_session: Session) -> list[AuditLog]:
    return list(
        db_session.execute(select(AuditLog).where(AuditLog.action == "role.update")).scalars()
    )


def _role(db_session: Session, code: str) -> Role:
    return db_session.execute(select(Role).where(Role.code == code)).scalar_one()


def test_update_role_success(db_session: Session) -> None:
    admin = make_staff(db_session, role_code="admin")
    tutor = _role(db_session, "tutor")
    original = list(tutor.permissions)
    assert "leaves:write" not in original

    out = update_role(
        db_session,
        tutor.id,
        RoleUpdateIn(permissions=[*original, "leaves:write"]),
        actor=_actor(admin.id),
        meta=_META,
    )

    assert isinstance(out, RoleOut)
    assert out.code == "tutor"
    assert out.is_system is True
    assert "leaves:write" in out.permissions
    assert out.permissions == sorted(out.permissions)
    assert "leaves:write" in out.effective_permissions
    assert sorted(_role(db_session, "tutor").permissions) == sorted([*original, "leaves:write"])
    logs = _update_logs(db_session)
    assert len(logs) == 1
    log = logs[0]
    assert log.entity_type == "role"
    assert log.entity_id == str(tutor.id)
    assert log.actor_id == admin.id
    assert log.ip == "203.0.113.5"
    assert log.before is not None
    assert log.after is not None
    # before / after 只含有變動的欄位
    assert set(log.before) == set(log.after) == {"permissions"}
    assert "leaves:write" not in log.before["permissions"]
    assert "leaves:write" in log.after["permissions"]

    # 只改名稱（description 未給不動）；同值欄位不進 before / after
    renamed = update_role(
        db_session,
        tutor.id,
        RoleUpdateIn(name="課輔教師", permissions=[*original, "leaves:write"]),
        actor=_actor(admin.id),
        meta=_META,
    )
    assert renamed.name == "課輔教師"
    assert renamed.description == tutor.description
    logs = _update_logs(db_session)
    assert len(logs) == 2
    assert logs[1].before == {"name": "課輔老師"}
    assert logs[1].after == {"name": "課輔教師"}

    # description 可清為 null；完全沒有變動時不寫稽核
    cleared = update_role(
        db_session, tutor.id, RoleUpdateIn(description=None), actor=_actor(admin.id), meta=_META
    )
    assert cleared.description is None
    assert len(_update_logs(db_session)) == 3
    update_role(
        db_session, tutor.id, RoleUpdateIn(name="課輔教師"), actor=_actor(admin.id), meta=_META
    )
    assert len(_update_logs(db_session)) == 3


def test_update_role_admin_protected(db_session: Session) -> None:
    admin = make_staff(db_session, role_code="admin")
    admin_role = _role(db_session, "admin")

    for data in (RoleUpdateIn(name="超管"), RoleUpdateIn(permissions=["roles:write"])):
        with pytest.raises(AppError) as exc:
            update_role(db_session, admin_role.id, data, actor=_actor(admin.id), meta=_META)
        assert (exc.value.status, exc.value.code) == (409, "system_role_protected")

    assert _role(db_session, "admin").name != "超管"
    assert _role(db_session, "admin").permissions == ["*"]
    assert _update_logs(db_session) == []
    # 其他系統角色可改
    director = _role(db_session, "director")
    assert (
        update_role(
            db_session,
            director.id,
            RoleUpdateIn(name="園長"),
            actor=_actor(admin.id),
            meta=_META,
        ).name
        == "園長"
    )


def test_update_role_not_found(db_session: Session) -> None:
    admin = make_staff(db_session, role_code="admin")

    with pytest.raises(AppError) as exc:
        update_role(db_session, uuid4(), RoleUpdateIn(name="x"), actor=_actor(admin.id), meta=_META)

    assert (exc.value.status, exc.value.code) == (404, "role_not_found")


def test_update_role_grant_limit(db_session: Session) -> None:
    make_staff(db_session, role_code="admin")
    director = make_staff(db_session)
    actor = _limited_actor(director.id, "roles:write", "pickup:read")
    role = make_role(db_session, code="custom_exam", permissions=["exams:publish"])

    # 移除自己沒有的碼不受限
    out = update_role(
        db_session, role.id, RoleUpdateIn(permissions=["pickup:read"]), actor=actor, meta=_META
    )
    assert out.permissions == ["pickup:read"]

    with pytest.raises(AppError) as exc:
        update_role(
            db_session,
            role.id,
            RoleUpdateIn(permissions=["pickup:read", "pickup:override"]),
            actor=actor,
            meta=_META,
        )

    assert (exc.value.status, exc.value.code) == (403, "cannot_grant_permissions")
    assert exc.value.details == {"permissions": ["pickup:override"]}
    assert _role(db_session, "custom_exam").permissions == ["pickup:read"]
    assert len(_update_logs(db_session)) == 1
    # 無效權限碼 422
    with pytest.raises(AppError) as invalid:
        update_role(
            db_session, role.id, RoleUpdateIn(permissions=["pickup:fly"]), actor=actor, meta=_META
        )
    assert (invalid.value.status, invalid.value.code) == (422, "unknown_permission")


def test_update_role_last_manager(db_session: Session) -> None:
    db_session.execute(update(StaffUser).values(is_active=False))
    mgr = make_role(db_session, code="mgr", permissions=["roles:write", "staff:write"])
    staff = make_staff(db_session)
    staff.role = mgr
    db_session.flush()
    actor = _limited_actor(staff.id, "roles:write", "staff:write")

    with pytest.raises(AppError) as exc:
        update_role(
            db_session, mgr.id, RoleUpdateIn(permissions=["staff:write"]), actor=actor, meta=_META
        )

    assert (exc.value.status, exc.value.code) == (409, "last_role_manager")
    assert _update_logs(db_session) == []
    # 保留 roles:write、拿掉 staff:write 同樣被擋
    with pytest.raises(AppError) as exc2:
        update_role(
            db_session, mgr.id, RoleUpdateIn(permissions=["roles:write"]), actor=actor, meta=_META
        )
    assert exc2.value.code == "last_staff_manager"
