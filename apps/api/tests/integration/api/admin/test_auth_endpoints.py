"""BACKEND-042：POST /api/admin/auth/login。"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.services.auth.throttle import AuthThrottles, get_auth_throttles
from tests.support.factories import make_staff
from tests.support.route_audit import admin_routes_without_permission

_URL = "/api/admin/auth/login"
_PASSWORD = "Passw0rd-Test1"  # noqa: S105  測試假值
AssertError = Callable[..., None]


def _body(username: str = "lin.teacher", password: str = _PASSWORD) -> dict[str, str]:
    return {"username": username, "password": password}


def test_admin_login_success(api_client: TestClient, db_session: Session) -> None:
    make_staff(db_session, username="lin.teacher", role_code="tutor", display_name="林老師")
    db_session.commit()

    resp = api_client.post(_URL, json=_body())

    assert resp.status_code == 200
    user = resp.json()["user"]
    assert user["username"] == "lin.teacher"
    assert user["display_name"] == "林老師"
    assert user["role"]["code"] == "tutor"
    assert "homework:write" in user["permissions"]
    assert user["permissions"] == sorted(user["permissions"])
    assert user["must_change_password"] is False
    access = resp.cookies.get("staff_access")
    refresh = resp.cookies.get("staff_refresh")
    assert access
    assert refresh
    assert access not in resp.text
    assert refresh not in resp.text
    set_cookie = resp.headers.get_list("set-cookie")
    assert all("HttpOnly" in c for c in set_cookie)
    # 登入後的 cookie 可直接用於守衛（access cookie 有效）
    assert set(resp.json()) == {"user"}


def test_admin_login_username_case_insensitive_and_must_change_password(
    api_client: TestClient, db_session: Session
) -> None:
    make_staff(db_session, username="front.desk", role_code="clerk", must_change_password=True)
    db_session.commit()

    resp = api_client.post(_URL, json=_body("Front.Desk"))

    assert resp.status_code == 200
    assert resp.json()["user"]["must_change_password"] is True


def test_admin_login_422(api_client: TestClient, assert_error: AssertError) -> None:
    missing = api_client.post(_URL, json={"username": "amy"})
    extra = api_client.post(_URL, json={**_body(), "remember": True})
    empty = api_client.post(_URL, json=_body(username=""))

    assert_error(missing, 422, "validation_error")
    assert_error(extra, 422, "validation_error")
    assert_error(empty, 422, "validation_error")
    assert "staff_access" not in missing.cookies


def test_admin_login_401(
    api_client: TestClient, db_session: Session, assert_error: AssertError
) -> None:
    make_staff(db_session, username="lin.teacher")
    make_staff(db_session, username="left.teacher", is_active=False)
    db_session.commit()

    wrong_password = api_client.post(_URL, json=_body(password="Wrong-Passw0rd1"))
    unknown_user = api_client.post(_URL, json=_body(username="nobody.here"))
    inactive = api_client.post(_URL, json=_body(username="left.teacher"))

    for resp in (wrong_password, unknown_user, inactive):
        assert_error(resp, 401, "invalid_credentials")
        assert resp.cookies.get("staff_access") is None
        assert resp.cookies.get("staff_refresh") is None
    # 三種失敗訊息相同，不洩漏帳號是否存在 / 停用
    assert (
        len({r.json()["error"]["message"] for r in (wrong_password, unknown_user, inactive)}) == 1
    )


def test_admin_login_403_foreign_origin(
    api_client: TestClient, db_session: Session, assert_error: AssertError
) -> None:
    make_staff(db_session, username="lin.teacher")
    db_session.commit()

    resp = api_client.post(_URL, json=_body(), headers={"Origin": "https://evil.test"})

    assert_error(resp, 403, "origin_forbidden")
    assert resp.cookies.get("staff_access") is None


def test_admin_login_429(
    app: FastAPI, api_client: TestClient, db_session: Session, assert_error: AssertError
) -> None:
    app.dependency_overrides[get_auth_throttles] = lambda: AuthThrottles()
    make_staff(db_session, username="lin.teacher")
    db_session.commit()

    for _ in range(5):
        assert_error(
            api_client.post(_URL, json=_body(password="Wrong-Passw0rd1")),
            401,
            "invalid_credentials",
        )
    locked = api_client.post(_URL, json=_body(password="Wrong-Passw0rd1"))
    # 鎖定中連正確密碼也拒絕
    still_locked = api_client.post(_URL, json=_body())

    assert_error(locked, 429, "too_many_attempts")
    assert locked.headers["Retry-After"] == "900"
    assert locked.json()["error"]["details"] == {"retry_after_seconds": 900}
    assert_error(still_locked, 429, "too_many_attempts")
    assert still_locked.cookies.get("staff_access") is None


def test_admin_login_route_registered_without_permission_guard(app: FastAPI) -> None:
    # /api/admin/auth/ 在白名單內；其餘後台路由必須掛權限守衛
    assert admin_routes_without_permission(app) == []
    assert "/api/admin/auth/login" in app.openapi()["paths"]
