"""BACKEND-093：GET /api/admin/staff-users（staff:read；分頁、搜尋、篩選）。
BACKEND-094：POST /api/admin/staff-users（staff:write；臨時密碼只回一次）。
BACKEND-095：GET /api/admin/staff-users/{staff_id}（staff:read）。
BACKEND-096 / 097 / 098：PATCH /{staff_id}、POST /{staff_id}/reset-password、
POST /{staff_id}/deactivate（staff:write）。
BACKEND-528：GET /api/admin/staff-users/options（classes:write 或 staff:read）。
BACKEND-522：POST /api/admin/staff-users/{staff_id}/activate（staff:write；臨時密碼只回一次）。"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.main import create_app
from app.models.account import Role, StaffUser
from app.models.audit import AuditLog
from tests.support.factories import make_role, make_staff
from tests.support.route_audit import admin_routes_without_permission

_URL = "/api/admin/staff-users"
StaffClientFactory = Callable[..., tuple[TestClient, StaffUser]]
AssertError = Callable[..., None]


def test_admin_staff_list_success(staff_client: StaffClientFactory, db_session: Session) -> None:
    lin = make_staff(db_session, username="lin.teacher", role_code="tutor", display_name="林老師")
    make_staff(db_session, username="chen.clerk", role_code="clerk", display_name="陳行政")
    make_staff(
        db_session, username="lin.b", role_code="tutor", display_name="離職", is_active=False
    )
    client, _ = staff_client(permissions=["staff:read"])

    resp = client.get(_URL, params={"q": "lin"})

    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"items", "total"}
    assert body["total"] == 2
    assert body["items"][0]["username"].startswith("lin")
    # 啟用在前
    assert [(u["username"], u["is_active"]) for u in body["items"]] == [
        ("lin.teacher", True),
        ("lin.b", False),
    ]
    first = body["items"][0]
    assert first["id"] == str(lin.id)
    assert first["role"]["code"] == "tutor"
    assert "homework:write" in first["effective_permissions"]
    assert set(first) == {
        "id",
        "username",
        "display_name",
        "phone",
        "email",
        "role",
        "extra_permissions",
        "revoked_permissions",
        "effective_permissions",
        "is_active",
        "must_change_password",
        "last_login_at",
        "created_at",
    }
    # 篩選 + 分頁
    inactive = client.get(_URL, params={"q": "lin", "is_active": "false"}).json()
    assert [u["username"] for u in inactive["items"]] == ["lin.b"]
    page = client.get(_URL, params={"q": "lin", "page": 2, "page_size": 1}).json()
    assert (page["total"], [u["username"] for u in page["items"]]) == (2, ["lin.b"])
    by_role = client.get(_URL, params={"role_id": str(lin.role_id), "q": "lin"}).json()
    assert {u["username"] for u in by_role["items"]} == {"lin.teacher", "lin.b"}


def test_admin_staff_list_422(staff_client: StaffClientFactory, assert_error: AssertError) -> None:
    client, _ = staff_client(permissions=["staff:read"])

    assert_error(client.get(_URL, params={"page_size": 500}), 422, "validation_error")
    assert_error(client.get(_URL, params={"role_id": "abc"}), 422, "validation_error")
    assert_error(client.get(_URL, params={"is_active": "maybe"}), 422, "validation_error")
    assert_error(client.get(_URL, params={"foo": "bar"}), 422, "validation_error")


def test_admin_staff_list_401(api_client: TestClient, assert_error: AssertError) -> None:
    assert_error(api_client.get(_URL), 401, "unauthenticated")


def test_admin_staff_list_403(staff_client: StaffClientFactory, assert_error: AssertError) -> None:
    client, _ = staff_client(permissions=["roles:read"])

    resp = client.get(_URL)

    assert_error(resp, 403, "permission_denied")
    assert resp.json()["error"]["details"] == {"required": ["staff:read"]}


def test_admin_staff_list_no_password_hash(
    staff_client: StaffClientFactory, db_session: Session
) -> None:
    make_staff(db_session, username="hash.check", role_code="tutor")
    client, _ = staff_client(permissions=["staff:read"])

    resp = client.get(_URL, params={"q": "hash.check"})

    assert resp.status_code == 200
    assert resp.json()["total"] == 1
    assert "$argon2id$" not in resp.text
    assert "password_hash" not in resp.text


def test_admin_staff_list_guard_registered(app: FastAPI) -> None:
    assert admin_routes_without_permission(app) == []
    assert "get" in app.openapi()["paths"][_URL]


# --- BACKEND-094：POST /api/admin/staff-users ----------------------------------------------------


def _tutor_id(db_session: Session) -> str:
    return str(db_session.execute(select(Role).where(Role.code == "tutor")).scalar_one().id)


def _create_body(db_session: Session, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "username": "wang.tutor",
        "display_name": "王老師",
        "role_id": _tutor_id(db_session),
    }
    body.update(overrides)
    return body


def test_admin_staff_create_success(
    api_client: TestClient,
    db_session: Session,
    login_staff: Callable[[TestClient, StaffUser], None],
    app: FastAPI,
) -> None:
    admin = make_staff(db_session, role_code="admin")
    db_session.commit()
    login_staff(api_client, admin)

    resp = api_client.post(_URL, json=_create_body(db_session, phone="0912-000-123"))

    assert resp.status_code == 201
    body = resp.json()
    assert set(body) == {"user", "temp_password"}
    user = body["user"]
    assert user["username"] == "wang.tutor"
    assert user["display_name"] == "王老師"
    assert user["role"]["code"] == "tutor"
    assert user["must_change_password"] is True
    assert user["is_active"] is True
    assert "homework:write" in user["effective_permissions"]
    assert len(body["temp_password"]) >= 12
    assert resp.headers["cache-control"] == "no-store"
    assert "$argon2id$" not in resp.text
    # 已 commit：重讀 DB、GET 查得到，audit 已寫
    db_session.expire_all()
    row = db_session.execute(
        select(StaffUser).where(StaffUser.username == "wang.tutor")
    ).scalar_one()
    assert (str(row.id), row.must_change_password, row.phone) == (user["id"], True, "0912-000-123")
    assert api_client.get(f"{_URL}/{row.id}").status_code == 200
    log = db_session.execute(
        select(AuditLog).where(
            AuditLog.action == "staff_user.create", AuditLog.entity_id == str(row.id)
        )
    ).scalar_one()
    assert log.actor_id == admin.id
    assert body["temp_password"] not in str(log.after)
    # 臨時密碼可登入（另一個 cookie jar）
    fresh = TestClient(app, base_url="http://testserver")
    login = fresh.post(
        "/api/admin/auth/login",
        json={"username": "wang.tutor", "password": body["temp_password"]},
    )
    assert login.status_code == 200
    assert login.json()["user"]["must_change_password"] is True


def test_admin_staff_create_422(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    client, _ = staff_client(permissions=["staff:write", "homework:read"])
    body = _create_body(db_session)

    missing_role = client.post(_URL, json={k: v for k, v in body.items() if k != "role_id"})
    bad_email = client.post(_URL, json={**body, "email": "bad"})
    extra = client.post(_URL, json={**body, "password": "Secret-123"})
    bad_username = client.post(_URL, json={**body, "username": "王老師"})
    unknown_role = client.post(_URL, json={**body, "role_id": str(uuid4())})

    for resp in (missing_role, bad_email, extra, bad_username):
        assert_error(resp, 422, "validation_error")
    assert_error(unknown_role, 422, "invalid_role")
    assert (
        db_session.execute(select(StaffUser).where(StaffUser.username == "wang.tutor")).first()
        is None
    )


def test_admin_staff_create_401(
    api_client: TestClient, db_session: Session, assert_error: AssertError
) -> None:
    assert_error(api_client.post(_URL, json=_create_body(db_session)), 401, "unauthenticated")


def test_admin_staff_create_403(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    reader, _ = staff_client(permissions=["staff:read"])
    limited, _ = staff_client(permissions=["staff:write", "students:read"])

    denied = reader.post(_URL, json=_create_body(db_session))
    beyond = limited.post(_URL, json=_create_body(db_session))

    assert_error(denied, 403, "permission_denied")
    assert denied.json()["error"]["details"] == {"required": ["staff:write"]}
    assert_error(beyond, 403, "cannot_grant_permissions")
    assert "homework:write" in beyond.json()["error"]["details"]["permissions"]
    assert (
        db_session.execute(select(StaffUser).where(StaffUser.username == "wang.tutor")).first()
        is None
    )


def test_admin_staff_create_409(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    make_staff(db_session, username="wang.tutor", role_code="tutor")
    client, _ = staff_client(permissions=["staff:write", "homework:read"])
    body = _create_body(db_session, role_id=str(_role_id(db_session, "homework:read")))
    db_session.commit()  # 自訂角色要先 commit 才看得到

    resp = client.post(_URL, json=body)

    assert_error(resp, 409, "username_taken")
    # 大小寫視為同一帳號
    assert_error(client.post(_URL, json={**body, "username": "Wang.Tutor"}), 409, "username_taken")


def _role_id(db_session: Session, *permissions: str) -> Any:
    return make_role(db_session, permissions=list(permissions)).id


# --- BACKEND-095：GET /api/admin/staff-users/{id} ------------------------------------------------


def test_admin_staff_get_success(staff_client: StaffClientFactory, db_session: Session) -> None:
    target = make_staff(
        db_session,
        username="chen.clerk",
        role_code="clerk",
        display_name="陳行政",
        extra_permissions=["audit:read"],
    )
    client, _ = staff_client(permissions=["staff:read"])

    resp = client.get(f"{_URL}/{target.id}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == str(target.id)
    assert body["username"] == "chen.clerk"
    assert body["display_name"] == "陳行政"
    assert body["role"]["code"] == "clerk"
    assert body["extra_permissions"] == ["audit:read"]
    assert "audit:read" in body["effective_permissions"]
    assert "password_hash" not in body
    assert "$argon2id$" not in resp.text
    assert set(body) == {
        "id",
        "username",
        "display_name",
        "phone",
        "email",
        "role",
        "extra_permissions",
        "revoked_permissions",
        "effective_permissions",
        "is_active",
        "must_change_password",
        "last_login_at",
        "created_at",
    }


def test_admin_staff_get_422(staff_client: StaffClientFactory, assert_error: AssertError) -> None:
    client, _ = staff_client(permissions=["staff:read"])

    assert_error(client.get(f"{_URL}/abc"), 422, "validation_error")


def test_admin_staff_get_401(api_client: TestClient, assert_error: AssertError) -> None:
    assert_error(api_client.get(f"{_URL}/{uuid4()}"), 401, "unauthenticated")


def test_admin_staff_get_403(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    target = make_staff(db_session, role_code="tutor")
    client, _ = staff_client(permissions=["roles:read"])

    resp = client.get(f"{_URL}/{target.id}")

    assert_error(resp, 403, "permission_denied")
    assert resp.json()["error"]["details"] == {"required": ["staff:read"]}


def test_admin_staff_get_404(staff_client: StaffClientFactory, assert_error: AssertError) -> None:
    client, _ = staff_client(permissions=["staff:read"])

    assert_error(client.get(f"{_URL}/{uuid4()}"), 404, "staff_user_not_found")


def test_admin_staff_write_guard_registered(app: FastAPI) -> None:
    assert admin_routes_without_permission(app) == []
    paths = app.openapi()["paths"]
    assert "post" in paths[_URL]
    assert "get" in paths[_URL + "/{staff_id}"]


# --- BACKEND-096 / 097 / 098：PATCH、reset-password、deactivate ---------------------------------


def _director_plus_staff_write(db_session: Session) -> list[str]:
    director = db_session.execute(select(Role).where(Role.code == "director")).scalar_one()
    return [*director.permissions, "staff:write"]


def _reload(db_session: Session, staff_id: Any) -> StaffUser:
    db_session.expire_all()
    staff = db_session.get(StaffUser, staff_id)
    assert staff is not None
    return staff


def test_admin_staff_update_success(staff_client: StaffClientFactory, db_session: Session) -> None:
    target = make_staff(db_session, role_code="tutor", display_name="王老師")
    admin, me = staff_client(role_code="admin")

    resp = admin.patch(f"{_URL}/{target.id}", json={"display_name": "王小美"})

    assert resp.status_code == 200
    body = resp.json()
    assert (body["id"], body["display_name"], body["role"]["code"]) == (
        str(target.id),
        "王小美",
        "tutor",
    )
    assert "password_hash" not in body
    assert _reload(db_session, target.id).display_name == "王小美"
    [log] = db_session.execute(
        select(AuditLog).where(
            AuditLog.action == "staff_user.update", AuditLog.entity_id == str(target.id)
        )
    ).scalars()
    assert (log.actor_id, log.after) == (me.id, {"display_name": "王小美"})


def test_admin_staff_update_422(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    target = make_staff(db_session, role_code="tutor")
    admin, _ = staff_client(role_code="admin")

    assert_error(admin.patch(f"{_URL}/{target.id}", json={}), 422, "validation_error")
    assert_error(
        admin.patch(f"{_URL}/{target.id}", json={"username": "x"}), 422, "validation_error"
    )
    assert_error(
        admin.patch(f"{_URL}/abc", json={"display_name": "王小美"}), 422, "validation_error"
    )


def test_admin_staff_update_401(api_client: TestClient, assert_error: AssertError) -> None:
    resp = api_client.patch(f"{_URL}/{uuid4()}", json={"display_name": "王小美"})

    assert_error(resp, 401, "unauthenticated")


def test_admin_staff_update_403(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    admin_target = make_staff(db_session, role_code="admin")
    tutor = make_staff(db_session, role_code="tutor", display_name="王老師")
    lesser, _ = staff_client(permissions=_director_plus_staff_write(db_session))
    reader, _ = staff_client(permissions=["staff:read"])

    cannot_manage = lesser.patch(f"{_URL}/{admin_target.id}", json={"display_name": "x"})
    denied = reader.patch(f"{_URL}/{tutor.id}", json={"display_name": "x"})
    # 授出自己沒有的權限
    beyond = lesser.patch(f"{_URL}/{tutor.id}", json={"extra_permissions": ["roles:write"]})

    assert_error(cannot_manage, 403, "cannot_manage_staff")
    assert_error(denied, 403, "permission_denied")
    assert denied.json()["error"]["details"] == {"required": ["staff:write"]}
    assert_error(beyond, 403, "cannot_grant_permissions")
    assert _reload(db_session, tutor.id).display_name == "王老師"


def test_admin_staff_update_409(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    clerk_role = db_session.execute(select(Role).where(Role.code == "clerk")).scalar_one()
    admin, me = staff_client(role_code="admin")

    own = admin.patch(f"{_URL}/{me.id}", json={"role_id": str(clerk_role.id)})
    missing = admin.patch(f"{_URL}/{uuid4()}", json={"display_name": "王小美"})

    assert_error(own, 409, "cannot_modify_self_permissions")
    assert_error(missing, 404, "staff_user_not_found")
    assert _reload(db_session, me.id).role.code == "admin"


def test_admin_staff_reset_success(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    target_client, target = staff_client(role_code="tutor")
    assert target_client.get("/api/admin/auth/me").status_code == 200
    admin, _ = staff_client(role_code="admin")

    resp = admin.post(f"{_URL}/{target.id}/reset-password")

    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"temp_password"}
    assert len(body["temp_password"]) == 12
    assert resp.headers["cache-control"] == "no-store"
    assert_error(target_client.get("/api/admin/auth/me"), 401, "unauthenticated")
    reloaded = _reload(db_session, target.id)
    assert (reloaded.must_change_password, reloaded.token_version) == (True, 1)


def test_admin_staff_reset_422(staff_client: StaffClientFactory, assert_error: AssertError) -> None:
    admin, _ = staff_client(role_code="admin")

    assert_error(admin.post(f"{_URL}/abc/reset-password"), 422, "validation_error")


def test_admin_staff_reset_401(api_client: TestClient, assert_error: AssertError) -> None:
    assert_error(api_client.post(f"{_URL}/{uuid4()}/reset-password"), 401, "unauthenticated")


def test_admin_staff_reset_403(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    target = make_staff(db_session, role_code="tutor")
    reader, _ = staff_client(permissions=["staff:read"])

    assert_error(reader.post(f"{_URL}/{target.id}/reset-password"), 403, "permission_denied")
    assert _reload(db_session, target.id).token_version == 0


def test_admin_staff_reset_404(staff_client: StaffClientFactory, assert_error: AssertError) -> None:
    admin, _ = staff_client(role_code="admin")

    assert_error(admin.post(f"{_URL}/{uuid4()}/reset-password"), 404, "staff_user_not_found")


def test_admin_staff_reset_409_self(
    staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    admin, me = staff_client(role_code="admin")

    assert_error(admin.post(f"{_URL}/{me.id}/reset-password"), 409, "cannot_reset_self")
    assert admin.get("/api/admin/auth/me").status_code == 200


def test_admin_staff_deactivate_success(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    target_client, target = staff_client(role_code="tutor")
    assert target_client.get("/api/admin/auth/me").status_code == 200
    admin, _ = staff_client(role_code="admin")

    resp = admin.post(f"{_URL}/{target.id}/deactivate")

    assert resp.status_code == 200
    assert (resp.json()["id"], resp.json()["is_active"]) == (str(target.id), False)
    assert_error(target_client.get("/api/admin/auth/me"), 401, "unauthenticated")
    reloaded = _reload(db_session, target.id)
    assert (reloaded.is_active, reloaded.token_version) == (False, 1)


def test_admin_staff_deactivate_422(
    staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    admin, _ = staff_client(role_code="admin")

    assert_error(admin.post(f"{_URL}/abc/deactivate"), 422, "validation_error")


def test_admin_staff_deactivate_401(api_client: TestClient, assert_error: AssertError) -> None:
    assert_error(api_client.post(f"{_URL}/{uuid4()}/deactivate"), 401, "unauthenticated")


def test_admin_staff_deactivate_403(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    target = make_staff(db_session, role_code="tutor")
    reader, _ = staff_client(permissions=["staff:read"])

    assert_error(reader.post(f"{_URL}/{target.id}/deactivate"), 403, "permission_denied")
    assert _reload(db_session, target.id).is_active is True


def test_admin_staff_deactivate_404(
    staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    admin, _ = staff_client(role_code="admin")

    assert_error(admin.post(f"{_URL}/{uuid4()}/deactivate"), 404, "staff_user_not_found")


def test_admin_staff_deactivate_409_self(
    staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    admin, me = staff_client(role_code="admin")

    assert_error(admin.post(f"{_URL}/{me.id}/deactivate"), 409, "cannot_deactivate_self")
    assert admin.get("/api/admin/auth/me").status_code == 200


def test_admin_staff_mutations_guard_registered(app: FastAPI) -> None:
    assert admin_routes_without_permission(app) == []
    paths = app.openapi()["paths"]
    assert "patch" in paths[_URL + "/{staff_id}"]
    assert "post" in paths[_URL + "/{staff_id}/reset-password"]
    assert "post" in paths[_URL + "/{staff_id}/deactivate"]


# --- BACKEND-528：GET /api/admin/staff-users/options ----------------------------------------------


def test_admin_staff_options_success(staff_client: StaffClientFactory, db_session: Session) -> None:
    active = make_staff(db_session, role_code="tutor", display_name="林老師")
    inactive = make_staff(db_session, role_code="tutor", display_name="離職老師", is_active=False)
    class_writer, _ = staff_client(permissions=["classes:write"])
    reader, _ = staff_client(permissions=["staff:read"])

    resp = class_writer.get(f"{_URL}/options")

    assert resp.status_code == 200
    body = resp.json()
    assert all(set(item) == {"id", "display_name"} for item in body)
    ids = {item["id"] for item in body}
    assert str(active.id) in ids
    assert str(inactive.id) not in ids
    assert {"id": str(active.id), "display_name": "林老師"} in body
    assert reader.get(f"{_URL}/options").json() == body


def test_admin_staff_options_route_order(
    staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    client, _ = staff_client(permissions=["staff:read"])

    assert client.get(f"{_URL}/options").status_code == 200
    assert_error(client.get(f"{_URL}/abc"), 422, "validation_error")


def test_admin_staff_options_401(api_client: TestClient, assert_error: AssertError) -> None:
    assert_error(api_client.get(f"{_URL}/options"), 401, "unauthenticated")


def test_admin_staff_options_403(
    staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    client, _ = staff_client(permissions=["classes:read"])

    resp = client.get(f"{_URL}/options")

    assert_error(resp, 403, "permission_denied")
    assert resp.json()["error"]["details"]["required"] == ["classes:write", "staff:read"]


def test_admin_staff_options_route_audit() -> None:
    assert f"{_URL}/options" not in admin_routes_without_permission(create_app())
    assert admin_routes_without_permission(create_app()) == []


# --- BACKEND-522：POST /api/admin/staff-users/{staff_id}/activate --------------------------------


def _activate_url(staff_id: object) -> str:
    return f"{_URL}/{staff_id}/activate"


def _deactivated(db_session: Session, **staff_kwargs: Any) -> StaffUser:
    """停用中的帳號（停用時 token_version 已 +1）。"""
    staff = make_staff(db_session, is_active=False, **staff_kwargs)
    staff.token_version = 1
    db_session.flush()
    return staff


def test_admin_staff_activate_guard_registered(app: FastAPI) -> None:
    assert admin_routes_without_permission(app) == []
    assert "post" in app.openapi()["paths"][_URL + "/{staff_id}/activate"]


def test_admin_staff_activate_success(
    api_client: TestClient,
    app: FastAPI,
    db_session: Session,
    login_staff: Callable[[TestClient, StaffUser], None],
    assert_error: AssertError,
) -> None:
    target = _deactivated(db_session, username="wang.back", role_code="tutor")
    admin = make_staff(db_session, role_code="admin")
    db_session.commit()
    # 指定真實來源 IP 才能驗 request meta 有注入到 audit
    client = TestClient(app, base_url="http://testserver", client=("203.0.113.5", 50000))
    login_staff(client, admin)

    resp = client.post(_activate_url(target.id))

    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"user", "temp_password"}
    user = body["user"]
    assert (user["id"], user["is_active"], user["must_change_password"]) == (
        str(target.id),
        True,
        True,
    )
    assert user["role"]["code"] == "tutor"
    assert len(body["temp_password"]) == 12
    assert resp.headers["cache-control"] == "no-store"
    assert "$argon2id$" not in resp.text
    assert "password_hash" not in resp.text
    # 已 commit：重讀 DB；token_version 維持停用時的值
    reloaded = _reload(db_session, target.id)
    assert (reloaded.is_active, reloaded.must_change_password, reloaded.token_version) == (
        True,
        True,
        1,
    )
    # 稽核：記錄操作者與來源 IP，before / after 不含臨時密碼與雜湊
    log = db_session.execute(
        select(AuditLog).where(
            AuditLog.action == "staff_user.activate", AuditLog.entity_id == str(target.id)
        )
    ).scalar_one()
    assert (log.actor_id, log.ip) == (admin.id, "203.0.113.5")
    assert (log.before, log.after) == (
        {"is_active": False},
        {"is_active": True, "must_change_password": True},
    )
    dumped = json.dumps([log.before, log.after])
    assert body["temp_password"] not in dumped
    assert reloaded.password_hash not in dumped
    # 臨時密碼只回這一次：再次啟用 409，不會產生新密碼
    again = client.post(_activate_url(target.id))
    assert_error(again, 409, "staff_already_active")
    assert "temp_password" not in again.text
    # 臨時密碼可登入，登入後被要求先改密碼；舊密碼失效
    fresh = TestClient(app, base_url="http://testserver")
    login = fresh.post(
        "/api/admin/auth/login", json={"username": "wang.back", "password": body["temp_password"]}
    )
    assert login.status_code == 200
    assert login.json()["user"]["must_change_password"] is True
    assert_error(fresh.get("/api/admin/students"), 403, "password_change_required")
    old = TestClient(app, base_url="http://testserver").post(
        "/api/admin/auth/login", json={"username": "wang.back", "password": "Passw0rd-Test1"}
    )
    assert_error(old, 401, "invalid_credentials")


def test_admin_staff_activate_422(
    staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    admin, _ = staff_client(role_code="admin")

    assert_error(admin.post(_activate_url("abc")), 422, "validation_error")


def test_admin_staff_activate_401(
    api_client: TestClient, db_session: Session, assert_error: AssertError
) -> None:
    target = _deactivated(db_session, role_code="tutor")
    db_session.commit()

    assert_error(api_client.post(_activate_url(target.id)), 401, "unauthenticated")
    assert _reload(db_session, target.id).is_active is False


def test_admin_staff_activate_403(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    tutor = _deactivated(db_session, role_code="tutor")
    admin_target = _deactivated(db_session, role_code="admin")
    reader, _ = staff_client(permissions=["staff:read"])
    lesser, _ = staff_client(permissions=_director_plus_staff_write(db_session))

    denied = reader.post(_activate_url(tutor.id))
    # 目標權限大於操作者（director + staff:write 不可啟用 admin）
    cannot_manage = lesser.post(_activate_url(admin_target.id))

    assert_error(denied, 403, "permission_denied")
    assert denied.json()["error"]["details"] == {"required": ["staff:write"]}
    assert_error(cannot_manage, 403, "cannot_manage_staff")
    assert "temp_password" not in denied.text + cannot_manage.text
    assert _reload(db_session, tutor.id).is_active is False
    assert _reload(db_session, admin_target.id).is_active is False


def test_admin_staff_activate_409(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    active = make_staff(db_session, role_code="tutor")
    admin, me = staff_client(role_code="admin")

    assert_error(admin.post(_activate_url(active.id)), 409, "staff_already_active")
    assert_error(admin.post(_activate_url(me.id)), 409, "staff_already_active")
    assert_error(admin.post(_activate_url(uuid4())), 404, "staff_user_not_found")
    reloaded = _reload(db_session, active.id)
    assert (reloaded.must_change_password, reloaded.token_version) == (False, 0)
