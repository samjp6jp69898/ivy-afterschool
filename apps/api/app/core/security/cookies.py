"""BACKEND-035：員工 / 家長 access、refresh 與綁定 cookie 的設定與清除。

員工與家長 cookie 名稱分開，同一瀏覽器可同時持有兩種登入（移植 ivy ``utils/cookie.py`` 的
staff / parent 分名做法）。前後端同源（nginx 反代），SameSite=Lax。

| 常數 | cookie 名 | path | max_age |
|---|---|---|---|
| STAFF_ACCESS | staff_access | /api | 15 分（ACCESS_TOKEN_TTL） |
| STAFF_REFRESH | staff_refresh | /api/admin/auth | 14 天 |
| PARENT_ACCESS | parent_access | /api | 15 分 |
| PARENT_REFRESH | parent_refresh | /api/parent/auth | 30 天 |
| PARENT_BIND | parent_bind | /api/parent/auth | 10 分（BIND_TOKEN_TTL） |

全部 ``httponly=True``、``samesite='lax'``、``secure=settings.is_production``；clear 時 path 與 set
相同，否則瀏覽器不會刪。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Final

from starlette.requests import HTTPConnection
from starlette.responses import Response

from app.core.config import Settings
from app.core.security.tokens import ACCESS_TOKEN_TTL, BIND_TOKEN_TTL, SubjectType

REFRESH_TTL: Final[dict[SubjectType, timedelta]] = {
    "staff": timedelta(days=14),
    "parent": timedelta(days=30),
}
_ACCESS_PATH: Final = "/api"
_SAMESITE: Final = "lax"


@dataclass(frozen=True)
class CookieSpec:
    name: str
    path: str
    max_age: int  # 秒


STAFF_ACCESS: Final = CookieSpec(
    "staff_access", _ACCESS_PATH, int(ACCESS_TOKEN_TTL.total_seconds())
)
STAFF_REFRESH: Final = CookieSpec(
    "staff_refresh", "/api/admin/auth", int(REFRESH_TTL["staff"].total_seconds())
)
PARENT_ACCESS: Final = CookieSpec(
    "parent_access", _ACCESS_PATH, int(ACCESS_TOKEN_TTL.total_seconds())
)
PARENT_REFRESH: Final = CookieSpec(
    "parent_refresh", "/api/parent/auth", int(REFRESH_TTL["parent"].total_seconds())
)
PARENT_BIND: Final = CookieSpec(
    "parent_bind", "/api/parent/auth", int(BIND_TOKEN_TTL.total_seconds())
)

_AUTH_COOKIES: Final[dict[SubjectType, tuple[CookieSpec, CookieSpec]]] = {
    "staff": (STAFF_ACCESS, STAFF_REFRESH),
    "parent": (PARENT_ACCESS, PARENT_REFRESH),
}


def _set(response: Response, spec: CookieSpec, value: str, settings: Settings) -> None:
    response.set_cookie(
        key=spec.name,
        value=value,
        max_age=spec.max_age,
        path=spec.path,
        secure=settings.is_production,
        httponly=True,
        samesite=_SAMESITE,
    )


def _clear(response: Response, spec: CookieSpec, settings: Settings) -> None:
    response.delete_cookie(
        key=spec.name,
        path=spec.path,
        secure=settings.is_production,
        httponly=True,
        samesite=_SAMESITE,
    )


def set_auth_cookies(
    response: Response,
    *,
    subject_type: SubjectType,
    access_token: str,
    refresh_token: str | None,
    settings: Settings,
) -> None:
    """refresh_token=None 時只設 access cookie（access 續期）。"""
    access_spec, refresh_spec = _AUTH_COOKIES[subject_type]
    _set(response, access_spec, access_token, settings)
    if refresh_token is not None:
        _set(response, refresh_spec, refresh_token, settings)


def clear_auth_cookies(
    response: Response, *, subject_type: SubjectType, settings: Settings
) -> None:
    for spec in _AUTH_COOKIES[subject_type]:
        _clear(response, spec, settings)


def set_bind_cookie(response: Response, token: str, *, settings: Settings) -> None:
    _set(response, PARENT_BIND, token, settings)


def clear_bind_cookie(response: Response, *, settings: Settings) -> None:
    _clear(response, PARENT_BIND, settings)


def read_cookie(request_or_ws: HTTPConnection, name: str) -> str | None:
    """Request 與 WebSocket 都是 HTTPConnection，cookie 解析相同。"""
    return request_or_ws.cookies.get(name)
