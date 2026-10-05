"""BACKEND-093：GET /api/admin/staff-users（staff:read；分頁、搜尋、篩選）。
BACKEND-094：POST /api/admin/staff-users（staff:write；臨時密碼只回一次）。
BACKEND-095：GET /api/admin/staff-users/{staff_id}（staff:read）。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

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
