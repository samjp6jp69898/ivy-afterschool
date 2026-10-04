"""BACKEND-023：API 整合測試 fixtures（所有 endpoint 整合測試共用），建立在 INFRA-010 的
``db_session`` 之上。

- ``app``：``create_app(settings=測試設定, start_background=False)``；dependency overrides：
  ``get_db`` → ``override_get_db(db_session)``（請求結束 rollback 到 savepoint）、``get_clock`` →
  ``fake_clock``、``get_storage`` → ``fake_storage``（同一個 FakeStorage 實例）。
- ``api_client``：未登入的 TestClient，不帶 Origin header（避開 BACKEND-019 的 Origin 檢查）。
  以 context manager 進入，lifespan 跑一次（tx hooks、broadcaster）；結束時還原 root logger 與
  broadcaster 單例。
- ``login_staff(client, staff)`` / ``login_parent(client, parent)``：直接設 access cookie
  （``tests/support/auth_cookies.py``），不走 /login。
- ``staff_client(permissions=[...])`` / ``parent_client()``：factory，每次呼叫都建立**獨立**
  TestClient（cookie jar 分開，兩個家長互不影響）並 ``db_session.commit()`` 釋放 savepoint，帳號才能
  跨請求存在。其他 ``make_*`` 的測資同樣要在打 API 前 commit。
- ``assert_error(resp, status, code)``：斷言錯誤 envelope。
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator, Sequence
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.clock import get_clock
from app.core.config import get_settings
from app.core.crypto import derive_key
from app.core.db import get_db
from app.core.storage import get_storage
from app.main import create_app
from app.models.account import StaffUser
from app.models.parents import ParentAccount
from app.realtime.broadcaster import reset_broadcaster_for_tests
from tests.support import auth_cookies
from tests.support.db_override import override_get_db
from tests.support.factories import make_parent, make_staff
from tests.support.fake_clock import FakeClock
from tests.support.fake_storage import FakeStorage

_BASE_URL = "http://testserver"
_TEST_ENV = {
    "APP_ENV": "test",
    "DATABASE_URL": "postgresql+psycopg://u:p@127.0.0.1:54342/postgres",
    "APP_SECRET_KEY": "s" * 48,
    "PUBLIC_BASE_URL": "http://127.0.0.1:5341",
    "R2_ENDPOINT_URL": "http://127.0.0.1:54344",
    "R2_ACCESS_KEY_ID": "afterschool",
    "R2_SECRET_ACCESS_KEY": "afterschool-local-secret",
    "R2_BUCKET": "afterschool-local",
}

LoginStaff = Callable[[TestClient, StaffUser], None]
LoginParent = Callable[[TestClient, ParentAccount], None]
StaffClientFactory = Callable[..., tuple[TestClient, StaffUser]]
ParentClientFactory = Callable[..., tuple[TestClient, ParentAccount]]


def _clear_caches() -> None:
    get_settings.cache_clear()
    derive_key.cache_clear()
    get_storage.cache_clear()


@pytest.fixture(autouse=True)
def _api_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for key, value in _TEST_ENV.items():
        monkeypatch.setenv(key, value)
    _clear_caches()
    yield
    _clear_caches()


@pytest.fixture
def fake_storage() -> FakeStorage:
    return FakeStorage()


@pytest.fixture
def app(db_session: Session, fake_clock: FakeClock, fake_storage: FakeStorage) -> FastAPI:
    app = create_app(settings=get_settings(), start_background=False)
    app.dependency_overrides[get_db] = override_get_db(db_session)
    app.dependency_overrides[get_clock] = lambda: fake_clock
    app.dependency_overrides[get_storage] = lambda: fake_storage
    return app


@pytest.fixture
def api_client(app: FastAPI) -> Iterator[TestClient]:
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    try:
        with TestClient(app, base_url=_BASE_URL) as client:
            yield client
    finally:
        for handler in list(root.handlers):
            if handler not in handlers:
                root.removeHandler(handler)
        for handler in handlers:
            if handler not in root.handlers:
                root.addHandler(handler)
        root.setLevel(level)
        reset_broadcaster_for_tests()


@pytest.fixture
def login_staff(fake_clock: FakeClock) -> LoginStaff:
    def _login(client: TestClient, staff: StaffUser) -> None:
        auth_cookies.login_staff(client, staff, clock=fake_clock)

    return _login


@pytest.fixture
def login_parent(fake_clock: FakeClock) -> LoginParent:
    def _login(client: TestClient, parent: ParentAccount) -> None:
        auth_cookies.login_parent(client, parent, clock=fake_clock)

    return _login


@pytest.fixture
def _extra_clients(app: FastAPI, api_client: TestClient) -> Iterator[Callable[[], TestClient]]:
    """與 api_client 共用同一個 app（lifespan 已由 api_client 啟動），cookie jar 獨立。"""
    clients: list[TestClient] = []

    def _new() -> TestClient:
        client = TestClient(app, base_url=_BASE_URL)
        clients.append(client)
        return client

    yield _new
    for client in clients:
        client.close()


@pytest.fixture
def staff_client(
    db_session: Session, login_staff: LoginStaff, _extra_clients: Callable[[], TestClient]
) -> StaffClientFactory:
    """``staff_client(permissions=[...], **make_staff 參數)`` → ``(client, staff)``，已 commit。"""

    def _factory(
        *, permissions: Sequence[str] | None = None, **staff_kwargs: Any
    ) -> tuple[TestClient, StaffUser]:
        staff = make_staff(db_session, permissions=permissions, **staff_kwargs)
        client = _extra_clients()
        login_staff(client, staff)
        db_session.commit()
        return client, staff

    return _factory


@pytest.fixture
def parent_client(
    db_session: Session, login_parent: LoginParent, _extra_clients: Callable[[], TestClient]
) -> ParentClientFactory:
    """``parent_client(**make_parent 參數)`` → ``(client, parent)``，已 commit，每次獨立 client。"""

    def _factory(**parent_kwargs: Any) -> tuple[TestClient, ParentAccount]:
        parent = make_parent(db_session, **parent_kwargs)
        client = _extra_clients()
        login_parent(client, parent)
        db_session.commit()
        return client, parent

    return _factory


@pytest.fixture
def assert_error() -> Callable[..., None]:
    return auth_cookies.assert_error
