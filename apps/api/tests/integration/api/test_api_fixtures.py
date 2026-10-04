"""BACKEND-023：tests/integration/api/conftest.py 的 API 整合測試 fixtures 自我驗證。

在 ``app`` fixture 上掛臨時探針路由（不依賴任何 endpoint task），驗證 staff_client /
parent_client / login helper / dependency overrides / assert_error 的行為。
"""

from collections.abc import Callable
from typing import Annotated, Any

import httpx2
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.api.deps import CurrentParent, CurrentStaff, get_current_parent, get_current_staff
from app.core.clock import get_clock
from app.core.storage import get_storage
from app.models.account import StaffUser
from app.models.parents import ParentAccount
from tests.support.fake_clock import FakeClock
from tests.support.fake_storage import FakeStorage

StaffClientFactory = Callable[..., tuple[TestClient, StaffUser]]
ParentClientFactory = Callable[..., tuple[TestClient, ParentAccount]]
AssertError = Callable[[httpx2.Response, int, str], None]


@pytest.fixture(autouse=True)
def _probes(app: FastAPI) -> None:
    @app.get("/__probe/staff")
    def staff_probe(staff: Annotated[CurrentStaff, Depends(get_current_staff)]) -> dict[str, Any]:
        return {"username": staff.username, "permissions": sorted(staff.permissions)}

    @app.get("/__probe/parent")
    def parent_probe(parent: Annotated[CurrentParent, Depends(get_current_parent)]) -> str:
        return str(parent.id)


def test_api_fixtures_login_staff_probe(staff_client: StaffClientFactory) -> None:
    client, staff = staff_client(permissions=["students:read"])

    response = client.get("/__probe/staff")

    assert response.status_code == 200
    assert response.json() == {"username": staff.username, "permissions": ["students:read"]}


def test_api_fixtures_parent_clients_isolated(parent_client: ParentClientFactory) -> None:
    c1, p1 = parent_client()
    c2, p2 = parent_client()
    assert p1.id != p2.id

    assert c1.get("/__probe/parent").json() == str(p1.id)
    assert c2.get("/__probe/parent").json() == str(p2.id)
    # cookie jar 分開：c1 的 cookie 不會跑到 c2
    assert c1.cookies.get("parent_access") != c2.cookies.get("parent_access")


def test_api_fixtures_clock_and_storage_overrides(
    app: FastAPI, fake_clock: FakeClock, fake_storage: FakeStorage
) -> None:
    assert app.dependency_overrides[get_clock]() is fake_clock
    assert app.dependency_overrides[get_storage]() is fake_storage


def test_api_fixtures_assert_error_helper(
    api_client: TestClient, assert_error: AssertError
) -> None:
    response = api_client.get("/__probe/staff")

    assert_error(response, 401, "unauthenticated")
    with pytest.raises(AssertionError):
        assert_error(response, 403, "unauthenticated")
    with pytest.raises(AssertionError):
        assert_error(response, 401, "permission_denied")


def test_api_fixtures_staff_client_survives_requests(staff_client: StaffClientFactory) -> None:
    """測資已 commit（釋放 savepoint）：第一個請求結束的 rollback 不會退掉帳號。"""
    client, staff = staff_client(permissions=["students:read"])

    first = client.get("/__probe/staff")
    second = client.get("/__probe/staff")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["username"] == staff.username
    assert second.json()["username"] == staff.username
