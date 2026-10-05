"""BACKEND-042 / 046 / 048：POST /api/admin/auth/login、POST /logout、GET /me。
BACKEND-044：POST /api/admin/auth/refresh。
BACKEND-050：POST /api/admin/auth/change-password。"""

from __future__ import annotations

from collections.abc import Callable

import httpx2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.clock import Clock
from app.core.errors import AppError
from app.core.permissions import Permission
from app.models.account import StaffUser
from app.models.parents import ParentAccount
from app.services.auth import refresh_tokens
from app.services.auth.throttle import AuthThrottles, get_auth_throttles
from tests.support.factories import make_staff
from tests.support.fake_clock import FakeClock
from tests.support.route_audit import admin_routes_without_permission

_URL = "/api/admin/auth/login"
_LOGOUT = "/api/admin/auth/logout"
_ME = "/api/admin/auth/me"
StaffClientFactory = Callable[..., tuple[TestClient, StaffUser]]
ParentClientFactory = Callable[..., tuple[TestClient, ParentAccount]]
_PASSWORD = "Passw0rd-Test1"  # noqa: S105  測試假值
_WRONG = "Wrong-Passw0rd1"
AssertError = Callable[..., None]


@pytest.fixture(autouse=True)
def throttles(app: FastAPI) -> AuthThrottles:
    """每個測試一份新的節流狀態：module 單例會在測試間累計（fake clock 時間固定）。"""
    instance = AuthThrottles()
    app.dependency_overrides[get_auth_throttles] = lambda: instance
    return instance


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

    wrong_password = api_client.post(_URL, json=_body(password=_WRONG))
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
    api_client: TestClient, db_session: Session, assert_error: AssertError
) -> None:
    make_staff(db_session, username="lin.teacher")
    db_session.commit()

    for _ in range(5):
        assert_error(
            api_client.post(_URL, json=_body(password=_WRONG)),
            401,
            "invalid_credentials",
        )
    locked = api_client.post(_URL, json=_body(password=_WRONG))
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


def _login(client: TestClient) -> str:
    resp = client.post(_URL, json=_body())
    assert resp.status_code == 200
    raw = resp.cookies.get("staff_refresh")
    assert raw
    return raw


def _cleared(resp: object, name: str) -> bool:
    assert isinstance(resp, httpx2.Response)
    return any(
        header.startswith(f'{name}="";') and "Max-Age=0" in header
        for header in resp.headers.get_list("set-cookie")
    )


def test_admin_logout_success(api_client: TestClient, db_session: Session) -> None:
    make_staff(db_session, username="lin.teacher")
    db_session.commit()
    _login(api_client)

    resp = api_client.post(_LOGOUT)

    assert resp.status_code == 200
    assert resp.json() == {"message": "已登出"}
    assert _cleared(resp, "staff_access")
    assert _cleared(resp, "staff_refresh")


def test_admin_logout_then_refresh_401(
    api_client: TestClient, db_session: Session, fake_clock: Clock
) -> None:
    make_staff(db_session, username="lin.teacher")
    db_session.commit()
    raw = _login(api_client)

    assert api_client.post(_LOGOUT).status_code == 200

    # 撤銷整個 family：舊 refresh 再輪替 → refresh_revoked（refresh endpoint 由 BACKEND-044 提供，
    # 這裡直接驗證 service 層的撤銷結果）
    with pytest.raises(AppError) as exc:
        refresh_tokens.rotate(db_session, raw, clock=fake_clock)
    assert (exc.value.status, exc.value.code) == (401, "refresh_revoked")


def test_admin_logout_no_cookie_200(api_client: TestClient) -> None:
    resp = api_client.post(_LOGOUT)

    assert resp.status_code == 200
    assert resp.json() == {"message": "已登出"}
    assert _cleared(resp, "staff_access")


def test_admin_logout_unknown_refresh_cookie_200(api_client: TestClient) -> None:
    api_client.cookies.set("staff_refresh", "garbage", path="/api/admin/auth")

    resp = api_client.post(_LOGOUT)

    assert resp.status_code == 200
    assert _cleared(resp, "staff_refresh")


def test_admin_logout_403_foreign_origin(
    api_client: TestClient, db_session: Session, fake_clock: Clock, assert_error: AssertError
) -> None:
    make_staff(db_session, username="lin.teacher")
    db_session.commit()
    raw = _login(api_client)

    resp = api_client.post(_LOGOUT, headers={"Origin": "https://evil.test"})

    assert_error(resp, 403, "origin_forbidden")
    # 被擋下的請求不得撤銷 token
    assert api_client.cookies.get("staff_refresh") == raw
    # refresh token 仍有效（沒被撤銷）
    assert refresh_tokens.rotate(db_session, raw, clock=fake_clock).subject_type == "staff"


def test_admin_logout_other_device_kept(
    api_client: TestClient,
    db_session: Session,
    fake_clock: Clock,
    app: FastAPI,
) -> None:
    make_staff(db_session, username="lin.teacher")
    db_session.commit()
    raw1 = _login(api_client)
    with TestClient(app, base_url="http://testserver") as other:
        raw2 = _login(other)
    assert raw1 != raw2

    assert api_client.post(_LOGOUT).status_code == 200

    rotated = refresh_tokens.rotate(db_session, raw2, clock=fake_clock)
    assert rotated.subject_type == "staff"
    with pytest.raises(AppError):
        refresh_tokens.rotate(db_session, raw1, clock=fake_clock)


def test_admin_me_success(staff_client: StaffClientFactory) -> None:
    client, staff = staff_client(
        permissions=["students:read", "classes:read"], display_name="林老師"
    )

    resp = client.get(_ME)

    assert resp.status_code == 200
    body = resp.json()
    assert body["permissions"] == ["classes:read", "students:read"]
    assert body["must_change_password"] is False
    assert body["id"] == str(staff.id)
    assert body["username"] == staff.username
    assert body["display_name"] == "林老師"
    assert set(body["role"]) == {"id", "code", "name"}
    assert set(body) == {
        "id",
        "username",
        "display_name",
        "role",
        "permissions",
        "must_change_password",
    }


def test_admin_me_permissions_sorted_with_extra_and_revoked(
    staff_client: StaffClientFactory,
) -> None:
    client, _ = staff_client(
        permissions=["students:read", "homework:write"],
        extra_permissions=["audit:read"],
        revoked_permissions=["homework:write"],
    )

    assert client.get(_ME).json()["permissions"] == ["audit:read", "students:read"]


def test_admin_me_admin_expands_wildcard(
    api_client: TestClient,
    db_session: Session,
    login_staff: Callable[[TestClient, StaffUser], None],
) -> None:
    admin = make_staff(db_session, role_code="admin")
    db_session.commit()
    login_staff(api_client, admin)

    body = api_client.get(_ME).json()

    assert body["permissions"] == sorted(p.value for p in Permission)
    assert "*" not in body["permissions"]
    assert body["role"]["code"] == "admin"


def test_admin_me_401(
    api_client: TestClient, parent_client: ParentClientFactory, assert_error: AssertError
) -> None:
    assert_error(api_client.get(_ME), 401, "unauthenticated")
    parent, _ = parent_client()
    assert_error(parent.get(_ME), 401, "unauthenticated")


def test_admin_me_must_change_password_allowed(staff_client: StaffClientFactory) -> None:
    client, _ = staff_client(permissions=["students:read"], must_change_password=True)

    resp = client.get(_ME)

    assert resp.status_code == 200
    assert resp.json()["must_change_password"] is True


def test_admin_me_inactive_401(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    client, staff = staff_client(permissions=["students:read"])
    assert client.get(_ME).status_code == 200
    staff.is_active = False
    db_session.commit()

    assert_error(client.get(_ME), 401, "unauthenticated")


# --- BACKEND-044：POST /api/admin/auth/refresh --------------------------------------------------

_REFRESH = "/api/admin/auth/refresh"


def _set_refresh(client: TestClient, raw: str) -> None:
    client.cookies.set("staff_refresh", raw, path="/api/admin/auth")


def test_admin_refresh_success(api_client: TestClient, db_session: Session) -> None:
    make_staff(db_session, username="lin.teacher", role_code="tutor", display_name="林老師")
    db_session.commit()
    old_refresh = _login(api_client)
    old_access = api_client.cookies.get("staff_access")

    resp = api_client.post(_REFRESH)

    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"user"}
    assert body["user"]["username"] == "lin.teacher"
    assert body["user"]["role"]["code"] == "tutor"
    assert body["user"]["permissions"] == sorted(body["user"]["permissions"])
    new_refresh = resp.cookies.get("staff_refresh")
    new_access = resp.cookies.get("staff_access")
    assert new_refresh
    assert new_refresh != old_refresh
    assert new_access
    assert new_access not in resp.text
    assert new_refresh not in resp.text
    # 新 refresh 已 commit：可再輪替一次；舊 access 仍未被撤銷（token_version 不變）
    assert api_client.post(_REFRESH).status_code == 200
    api_client.cookies.set("staff_access", old_access or "")
    assert api_client.get(_ME).status_code == 200


def test_admin_refresh_401_no_cookie(api_client: TestClient, assert_error: AssertError) -> None:
    api_client.cookies.clear()

    resp = api_client.post(_REFRESH)

    assert_error(resp, 401, "unauthenticated")
    assert _cleared(resp, "staff_access")
    assert _cleared(resp, "staff_refresh")


def test_admin_refresh_reuse_401(
    api_client: TestClient, db_session: Session, fake_clock: FakeClock, assert_error: AssertError
) -> None:
    make_staff(db_session, username="lin.teacher")
    db_session.commit()
    old_refresh = _login(api_client)
    assert api_client.post(_REFRESH).status_code == 200
    fake_clock.advance(seconds=6)
    _set_refresh(api_client, old_refresh)

    resp = api_client.post(_REFRESH)

    assert_error(resp, 401, "refresh_reused")
    assert _cleared(resp, "staff_access")
    assert _cleared(resp, "staff_refresh")
    # 整個 family 已撤銷且 token_version +1：新 refresh 與 access 全部失效
    with pytest.raises(AppError) as exc:
        refresh_tokens.rotate(db_session, old_refresh, clock=fake_clock)
    assert exc.value.code == "refresh_revoked"
    assert api_client.get(_ME).status_code == 401


def test_admin_refresh_409_in_progress(
    api_client: TestClient, db_session: Session, fake_clock: FakeClock, assert_error: AssertError
) -> None:
    make_staff(db_session, username="lin.teacher")
    db_session.commit()
    old_refresh = _login(api_client)
    first = api_client.post(_REFRESH)
    assert first.status_code == 200
    new_refresh = first.cookies.get("staff_refresh")
    fake_clock.advance(seconds=1)
    _set_refresh(api_client, old_refresh)

    resp = api_client.post(_REFRESH)

    assert_error(resp, 409, "refresh_in_progress")
    assert resp.headers.get_list("set-cookie") == []
    # 併發容忍：family 未撤銷，新 refresh 仍可用
    assert new_refresh
    _set_refresh(api_client, new_refresh)
    assert api_client.post(_REFRESH).status_code == 200


def test_admin_refresh_403_foreign_origin(
    api_client: TestClient, db_session: Session, fake_clock: FakeClock, assert_error: AssertError
) -> None:
    make_staff(db_session, username="lin.teacher")
    db_session.commit()
    raw = _login(api_client)

    resp = api_client.post(_REFRESH, headers={"Origin": "https://evil.test"})

    assert_error(resp, 403, "origin_forbidden")
    assert resp.headers.get_list("set-cookie") == []
    # 被擋下的請求沒有輪替 token
    assert refresh_tokens.rotate(db_session, raw, clock=fake_clock).subject_type == "staff"


def test_admin_refresh_after_logout_401_other_device_kept(
    api_client: TestClient, db_session: Session, app: FastAPI, assert_error: AssertError
) -> None:
    make_staff(db_session, username="lin.teacher")
    db_session.commit()
    raw1 = _login(api_client)
    with TestClient(app, base_url="http://testserver") as other:
        _login(other)
        assert api_client.post(_LOGOUT).status_code == 200

        # 登出後的裝置：舊 refresh 經 endpoint 輪替 → 401 refresh_revoked 並清 cookie
        _set_refresh(api_client, raw1)
        resp = api_client.post(_REFRESH)
        assert_error(resp, 401, "refresh_revoked")
        assert _cleared(resp, "staff_refresh")

        # 其他裝置的 family 不受影響
        assert other.post(_REFRESH).status_code == 200


# --- BACKEND-050：POST /api/admin/auth/change-password ------------------------------------------

_CHANGE = "/api/admin/auth/change-password"
_NEW_PASSWORD = "Afterschool2026"  # noqa: S105  測試假值


def _change_body(current: str = _PASSWORD, new: str = _NEW_PASSWORD) -> dict[str, str]:
    return {"current_password": current, "new_password": new}


def test_admin_change_password_success(
    api_client: TestClient, db_session: Session, fake_clock: FakeClock
) -> None:
    make_staff(db_session, username="lin.teacher", role_code="tutor", must_change_password=True)
    db_session.commit()
    old_refresh = _login(api_client)
    old_access = api_client.cookies.get("staff_access")
    assert old_access
    assert api_client.get(_ME).json()["must_change_password"] is True

    resp = api_client.post(_CHANGE, json=_change_body())

    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"user"}
    assert body["user"]["username"] == "lin.teacher"
    assert body["user"]["must_change_password"] is False
    assert body["user"]["permissions"] == sorted(body["user"]["permissions"])
    new_access = resp.cookies.get("staff_access")
    new_refresh = resp.cookies.get("staff_refresh")
    assert new_access
    assert new_refresh
    assert new_access != old_access
    assert new_refresh != old_refresh
    assert new_access not in resp.text
    # 舊 access（token_version 已 +1）→ 401；新 cookie → 200 且不再要求改密碼
    api_client.cookies.set("staff_access", old_access)
    assert api_client.get(_ME).status_code == 401
    api_client.cookies.set("staff_access", new_access)
    me = api_client.get(_ME)
    assert me.status_code == 200
    assert me.json()["must_change_password"] is False
    # 舊 refresh family 已撤銷、新 refresh 可用；新密碼可登入
    with pytest.raises(AppError) as exc:
        refresh_tokens.rotate(db_session, old_refresh, clock=fake_clock)
    assert exc.value.code == "refresh_revoked"
    assert api_client.post(_REFRESH).status_code == 200
    assert api_client.post(_URL, json=_body(password=_NEW_PASSWORD)).status_code == 200


def test_admin_change_password_422(
    staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    client, _ = staff_client(permissions=["students:read"])

    missing = client.post(_CHANGE, json={"current_password": _PASSWORD})
    extra = client.post(_CHANGE, json={**_change_body(), "confirm": _NEW_PASSWORD})
    weak = client.post(_CHANGE, json=_change_body(new="abc"))

    assert_error(missing, 422, "validation_error")
    assert_error(extra, 422, "validation_error")
    assert_error(weak, 422, "weak_password")
    assert len(weak.json()["error"]["details"]["reasons"]) >= 1
    # 密碼不回顯
    assert _PASSWORD not in missing.text
    assert "abc" not in weak.text


def test_admin_change_password_401(api_client: TestClient, assert_error: AssertError) -> None:
    assert_error(api_client.post(_CHANGE, json=_change_body()), 401, "unauthenticated")


def test_admin_change_password_403_foreign_origin(
    staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    client, _ = staff_client(permissions=["students:read"])

    resp = client.post(_CHANGE, json=_change_body(), headers={"Origin": "https://evil.test"})

    assert_error(resp, 403, "origin_forbidden")
    # 密碼未被改掉：原 access 仍有效（token_version 不變）
    assert client.get(_ME).status_code == 200


def test_admin_change_password_400_wrong_current(
    staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    client, _ = staff_client(permissions=["students:read"])

    resp = client.post(_CHANGE, json=_change_body(current="bad-Passw0rd"))

    assert_error(resp, 400, "current_password_incorrect")
    assert resp.headers.get_list("set-cookie") == []
    assert client.get(_ME).status_code == 200
