"""BACKEND-073：app/api/deps.py 的 require_permission / require_any_permission 與
tests/support/route_audit.py 的 admin_routes_without_permission。

在 BACKEND-023 的 ``app`` fixture 上掛探針路由；route audit 另以迷你 app 與 create_app() 驗證。
"""

from collections.abc import Callable
from typing import Annotated, Any

import httpx2
import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient

from app.api.deps import CurrentStaff, require_any_permission, require_permission
from app.core.permissions import Permission
from app.main import create_app
from app.models.account import StaffUser
from tests.support.route_audit import admin_routes_without_permission

StaffClientFactory = Callable[..., tuple[TestClient, StaffUser]]
AssertError = Callable[[httpx2.Response, int, str], None]


@pytest.fixture(autouse=True)
def _probes(app: FastAPI) -> None:
    @app.get("/api/admin/all-of")
    def all_of(
        staff: Annotated[
            CurrentStaff,
            Depends(require_permission(Permission.STUDENTS_READ, Permission.STUDENTS_WRITE)),
        ],
    ) -> dict[str, Any]:
        return {"username": staff.username}

    @app.get("/api/admin/any-of")
    def any_of(
        staff: Annotated[
            CurrentStaff,
            Depends(require_any_permission(Permission.ROLES_READ, Permission.STAFF_READ)),
        ],
    ) -> dict[str, Any]:
        return {"username": staff.username}


def test_require_permission_all_of(
    staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    client, staff = staff_client(permissions=["students:read", "students:write"])
    response = client.get("/api/admin/all-of")
    assert response.status_code == 200
    assert response.json() == {"username": staff.username}

    partial, _ = staff_client(permissions=["students:read"])
    denied = partial.get("/api/admin/all-of")
    assert_error(denied, 403, "permission_denied")
    assert denied.json()["error"]["details"] == {"required": ["students:read", "students:write"]}
    assert denied.json()["error"]["message"] == "您沒有此功能的權限"


def test_require_any_permission(
    staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    client, staff = staff_client(permissions=["staff:read"])
    response = client.get("/api/admin/any-of")
    assert response.status_code == 200
    assert response.json() == {"username": staff.username}

    none, _ = staff_client(permissions=["students:read"])
    denied = none.get("/api/admin/any-of")
    assert_error(denied, 403, "permission_denied")
    assert denied.json()["error"]["details"] == {"required": ["roles:read", "staff:read"]}


def test_require_permission_unauthenticated(
    api_client: TestClient, assert_error: AssertError
) -> None:
    assert_error(api_client.get("/api/admin/all-of"), 401, "unauthenticated")
    assert_error(api_client.get("/api/admin/any-of"), 401, "unauthenticated")


def test_require_permission_type_check() -> None:
    with pytest.raises(TypeError):
        require_permission("students:read")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        require_permission()
    with pytest.raises(TypeError):
        require_any_permission("students:read")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        require_any_permission()
    with pytest.raises(TypeError):
        require_permission(Permission.STUDENTS_READ, "students:write")  # type: ignore[arg-type]


def test_require_permission_route_audit() -> None:
    mini = FastAPI()

    @mini.get(
        "/api/admin/x", dependencies=[Depends(require_permission(Permission.STUDENTS_READ))]
    )
    def guarded() -> str:
        return "x"

    @mini.get("/api/admin/y")
    def unguarded() -> str:
        return "y"

    @mini.get("/api/admin/auth/me")
    def auth_me() -> str:
        return "me"

    @mini.get("/api/admin/notifications")
    def notifications() -> str:
        return "n"

    @mini.post("/api/admin/notifications/{notification_id}/read")
    def notifications_read(notification_id: str) -> str:
        return notification_id

    @mini.get("/api/parent/children")
    def parent_route() -> str:
        return "p"

    assert admin_routes_without_permission(mini) == ["/api/admin/y"]

    # include_router 是 lazy（app.routes 不攤平子路由）：守衛掛在 router 層也要被看見
    nested = FastAPI()
    sub = APIRouter(prefix="/api/admin/sub")

    @sub.get("/z", dependencies=[Depends(require_any_permission(Permission.ROLES_READ))])
    def guarded_any() -> str:
        return "z"

    @sub.get("/w")
    def unguarded_nested() -> str:
        return "w"

    guarded_router = APIRouter(
        prefix="/api/admin/guarded",
        dependencies=[Depends(require_permission(Permission.ROLES_READ))],
    )

    @guarded_router.get("/v")
    def guarded_by_router() -> str:
        return "v"

    nested.include_router(sub)
    nested.include_router(guarded_router)
    assert admin_routes_without_permission(nested) == ["/api/admin/sub/w"]
    assert admin_routes_without_permission(create_app(start_background=False)) == []
