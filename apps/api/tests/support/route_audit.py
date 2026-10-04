"""BACKEND-073：掃描 app 上未掛權限守衛的 ``/api/admin/`` 路由。

``admin_routes_without_permission(app)`` 回傳排序後的路徑清單：以 ``/api/admin/`` 開頭、不在白名單
（``/api/admin/auth/`` 底下與個人收件匣 ``/api/admin/notifications``）、且依賴樹（含 router 層的
``dependencies``）中沒有 ``require_permission`` / ``require_any_permission`` 產生的 dependency。

FastAPI 0.142 的 ``include_router`` 是 lazy，``app.routes`` 不攤平子路由，改用
``fastapi.routing.iter_route_contexts`` 取得含 prefix 與 router 層依賴的有效路由。
每個 admin endpoint task 的測試都應呼叫一次本函式並斷言為空清單。
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute, iter_route_contexts

from app.api.deps import PERMISSION_GUARD_ATTR

ADMIN_PREFIX = "/api/admin/"
# 不需權限碼的後台路徑：認證（登入即可或不需登入）與個人收件匣（只看自己的通知）
_WHITELIST_PREFIXES = ("/api/admin/auth/",)
_WHITELIST_PATHS = ("/api/admin/notifications",)


def _is_whitelisted(path: str) -> bool:
    if path.startswith(_WHITELIST_PREFIXES):
        return True
    return any(path == p or path.startswith(p + "/") for p in _WHITELIST_PATHS)


def _has_permission_guard(dependant: Dependant | None) -> bool:
    if dependant is None:
        return False
    stack = [dependant]
    while stack:
        current = stack.pop()
        if getattr(current.call, PERMISSION_GUARD_ATTR, False):
            return True
        stack.extend(current.dependencies)
    return False


def admin_routes_without_permission(app: FastAPI) -> list[str]:
    missing: set[str] = set()
    for ctx in iter_route_contexts(app.routes):
        if not isinstance(ctx.original_route, APIRoute):
            continue
        path = ctx.path
        if path is None or not path.startswith(ADMIN_PREFIX) or _is_whitelisted(path):
            continue
        # 經 include_router 的路由，ctx.dependant 是合併 router 層 dependencies 後的有效 dependant
        dependant: Dependant | None = getattr(ctx, "dependant", None)
        if not _has_permission_guard(dependant):
            missing.add(path)
    return sorted(missing)
