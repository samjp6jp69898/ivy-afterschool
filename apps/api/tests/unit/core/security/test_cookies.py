"""BACKEND-035：app/core/security/cookies.py（員工 / 家長 access、refresh 與綁定 cookie）。"""

from datetime import timedelta

from fastapi import Response
from starlette.requests import Request

from app.core.config import Settings
from app.core.security.cookies import (
    PARENT_ACCESS,
    PARENT_BIND,
    PARENT_REFRESH,
    REFRESH_TTL,
    STAFF_ACCESS,
    STAFF_REFRESH,
    clear_auth_cookies,
    clear_bind_cookie,
    read_cookie,
    set_auth_cookies,
    set_bind_cookie,
)

_LOCAL_R2_SECRET = "afterschool-local-secret"  # noqa: S105  本機 SeaweedFS 固定開發值
_ACCESS = "a"  # noqa: S105  假 token 值
_REFRESH = "r"  # noqa: S105  假 token 值


def _settings(app_env: str) -> Settings:
    cloud = app_env == "production"
    return Settings(
        _env_file=None,
        app_env=app_env,
        database_url="postgresql+psycopg://u:p@127.0.0.1:54342/postgres",
        app_secret_key="s" * 48,
        public_base_url="http://127.0.0.1:5341",
        r2_endpoint_url=(
            "https://acct.r2.cloudflarestorage.com" if cloud else "http://127.0.0.1:54344"
        ),
        r2_access_key_id="afterschool",
        r2_secret_access_key="k" * 40 if cloud else _LOCAL_R2_SECRET,
        r2_bucket="afterschool-local",
    )


def _cookies(response: Response) -> dict[str, str]:
    """Set-Cookie header 依 cookie 名稱索引。"""
    out: dict[str, str] = {}
    for raw in response.headers.getlist("set-cookie"):
        name = raw.split("=", 1)[0]
        assert name not in out, f"同一個 cookie 設了兩次：{name}"
        out[name] = raw
    return out


def _attrs(raw: str) -> set[str]:
    return {part.strip().lower() for part in raw.split(";")[1:]}


def test_cookies_staff_set() -> None:
    response = Response()
    set_auth_cookies(
        response,
        subject_type="staff",
        access_token=_ACCESS,
        refresh_token=_REFRESH,
        settings=_settings("development"),
    )

    cookies = _cookies(response)
    assert set(cookies) == {"staff_access", "staff_refresh"}
    assert cookies["staff_access"].startswith("staff_access=a;")
    assert {"path=/api", "max-age=900", "httponly", "samesite=lax"} <= _attrs(
        cookies["staff_access"]
    )
    assert cookies["staff_refresh"].startswith("staff_refresh=r;")
    assert {"path=/api/admin/auth", "max-age=1209600", "httponly", "samesite=lax"} <= _attrs(
        cookies["staff_refresh"]
    )
    assert "secure" not in _attrs(cookies["staff_access"])
    assert "secure" not in _attrs(cookies["staff_refresh"])
    assert STAFF_ACCESS.name == "staff_access"
    assert STAFF_REFRESH.path == "/api/admin/auth"
    assert REFRESH_TTL["staff"] == timedelta(days=14)
    assert REFRESH_TTL["parent"] == timedelta(days=30)


def test_cookies_parent_production_secure() -> None:
    response = Response()
    set_auth_cookies(
        response,
        subject_type="parent",
        access_token=_ACCESS,
        refresh_token=_REFRESH,
        settings=_settings("production"),
    )

    cookies = _cookies(response)
    assert set(cookies) == {"parent_access", "parent_refresh"}
    assert {"path=/api", "max-age=900", "httponly", "samesite=lax", "secure"} <= _attrs(
        cookies["parent_access"]
    )
    assert {"path=/api/parent/auth", "max-age=2592000", "httponly", "samesite=lax", "secure"} <= (
        _attrs(cookies["parent_refresh"])
    )
    assert PARENT_ACCESS.name == "parent_access"
    assert PARENT_REFRESH.max_age == 30 * 24 * 3600


def test_cookies_clear_same_path() -> None:
    response = Response()
    clear_auth_cookies(response, subject_type="staff", settings=_settings("development"))

    cookies = _cookies(response)
    assert set(cookies) == {"staff_access", "staff_refresh"}
    assert {"path=/api", "max-age=0", "httponly", "samesite=lax"} <= _attrs(cookies["staff_access"])
    assert {"path=/api/admin/auth", "max-age=0", "httponly", "samesite=lax"} <= _attrs(
        cookies["staff_refresh"]
    )

    parent = Response()
    clear_auth_cookies(parent, subject_type="parent", settings=_settings("production"))
    parent_cookies = _cookies(parent)
    assert set(parent_cookies) == {"parent_access", "parent_refresh"}
    assert {"path=/api/parent/auth", "max-age=0", "secure"} <= _attrs(
        parent_cookies["parent_refresh"]
    )


def test_cookies_access_only() -> None:
    response = Response()
    set_auth_cookies(
        response,
        subject_type="staff",
        access_token=_ACCESS,
        refresh_token=None,
        settings=_settings("development"),
    )

    cookies = _cookies(response)
    assert set(cookies) == {"staff_access"}
    assert cookies["staff_access"].startswith("staff_access=a;")


def test_cookies_bind() -> None:
    response = Response()
    set_bind_cookie(response, "t", settings=_settings("development"))
    cookies = _cookies(response)
    assert set(cookies) == {"parent_bind"}
    assert cookies["parent_bind"].startswith("parent_bind=t;")
    assert {"path=/api/parent/auth", "max-age=600", "httponly", "samesite=lax"} <= _attrs(
        cookies["parent_bind"]
    )
    assert "secure" not in _attrs(cookies["parent_bind"])

    cleared = Response()
    clear_bind_cookie(cleared, settings=_settings("production"))
    cleared_cookies = _cookies(cleared)
    assert set(cleared_cookies) == {"parent_bind"}
    assert {"path=/api/parent/auth", "max-age=0", "httponly", "secure"} <= _attrs(
        cleared_cookies["parent_bind"]
    )
    assert PARENT_BIND.max_age == 600


def test_cookies_read_cookie() -> None:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/x",
        "query_string": b"",
        "headers": [(b"cookie", b"staff_access=tok-1; parent_bind=b-2")],
    }
    request = Request(scope)

    assert read_cookie(request, STAFF_ACCESS.name) == "tok-1"
    assert read_cookie(request, PARENT_BIND.name) == "b-2"
    assert read_cookie(request, PARENT_ACCESS.name) is None
