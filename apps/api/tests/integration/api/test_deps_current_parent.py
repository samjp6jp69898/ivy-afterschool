"""BACKEND-062：app/api/deps.py 家長守衛（get_current_parent / get_optional_parent /
load_current_parent）。

迷你 app：get_db → override_get_db(db_session)、get_clock → fake_clock。守衛只驗身分與帳號狀態，
不檢查有無綁定小孩（無小孩的家長仍要能呼叫 /me 與 /bind）。
"""

from collections.abc import Iterator
from typing import Annotated, Any
from uuid import uuid4

import httpx2
import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.deps import (
    CurrentParent,
    get_current_parent,
    get_optional_parent,
    load_current_parent,
)
from app.core.clock import get_clock
from app.core.config import get_settings
from app.core.crypto import derive_key
from app.core.db import get_db
from app.core.errors import AppError, register_exception_handlers
from app.core.security.cookies import PARENT_ACCESS, STAFF_ACCESS
from app.core.security.tokens import SubjectType, create_access_token
from app.models.parents import ParentAccount
from tests.support.db_override import override_get_db
from tests.support.factories import make_guardian, make_parent, make_staff, make_student
from tests.support.fake_clock import FakeClock

Parent = Annotated[CurrentParent, Depends(get_current_parent)]
OptionalParent = Annotated[CurrentParent | None, Depends(get_optional_parent)]


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@127.0.0.1:54342/postgres")
    monkeypatch.setenv("APP_SECRET_KEY", "s" * 48)
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://127.0.0.1:5341")
    monkeypatch.setenv("R2_ENDPOINT_URL", "http://127.0.0.1:54344")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "afterschool")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "afterschool-local-secret")
    monkeypatch.setenv("R2_BUCKET", "afterschool-local")
    get_settings.cache_clear()
    derive_key.cache_clear()
    yield
    get_settings.cache_clear()
    derive_key.cache_clear()


@pytest.fixture
def client(db_session: Session, fake_clock: FakeClock) -> Iterator[TestClient]:
    app = FastAPI()
    register_exception_handlers(app)
    app.dependency_overrides[get_db] = override_get_db(db_session)
    app.dependency_overrides[get_clock] = lambda: fake_clock

    @app.get("/api/parent/probe")
    def probe(request: Request, parent: Parent) -> dict[str, Any]:
        return {
            "id": str(parent.id),
            "line_user_id": parent.line_user_id,
            "display_name": parent.display_name,
            "token_version": parent.token_version,
            "state_is_same": request.state.current_parent is parent,
        }

    @app.get("/api/parent/optional")
    def optional(parent: OptionalParent) -> str:
        return "none" if parent is None else str(parent.id)

    with TestClient(app) as c:
        yield c


def _token(
    parent: ParentAccount, fake_clock: FakeClock, *, subject_type: SubjectType = "parent"
) -> str:
    return create_access_token(
        subject_type=subject_type,
        subject_id=parent.id,
        token_version=parent.token_version,
        clock=fake_clock,
    )


def _get(
    client: TestClient, path: str, token: str | None, *, name: str = PARENT_ACCESS.name
) -> httpx2.Response:
    headers = {"Cookie": f"{name}={token}"} if token is not None else {}
    return client.get(path, headers=headers)


def _assert_401(response: httpx2.Response) -> None:
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"


def test_current_parent_ok(client: TestClient, db_session: Session, fake_clock: FakeClock) -> None:
    p = make_parent(db_session, display_name="王媽媽")
    make_guardian(db_session, make_student(db_session), parent=p)
    db_session.commit()

    response = _get(client, "/api/parent/probe", _token(p, fake_clock))

    assert response.status_code == 200
    assert response.json() == {
        "id": str(p.id),
        "line_user_id": p.line_user_id,
        "display_name": "王媽媽",
        "token_version": 0,
        "state_is_same": True,
    }


def test_current_parent_rejects(
    client: TestClient, db_session: Session, fake_clock: FakeClock
) -> None:
    p = make_parent(db_session)
    staff = make_staff(db_session, permissions=["students:read"])
    db_session.commit()
    token = _token(p, fake_clock)
    assert _get(client, "/api/parent/probe", token).status_code == 200

    _assert_401(_get(client, "/api/parent/probe", None))
    _assert_401(_get(client, "/api/parent/probe", "garbage"))
    # 員工 token（typ=staff）放在 parent_access
    staff_token = create_access_token(
        subject_type="staff", subject_id=staff.id, token_version=0, clock=fake_clock
    )
    _assert_401(_get(client, "/api/parent/probe", staff_token))
    # 家長 token 放錯 cookie 名稱（員工 cookie）
    _assert_401(_get(client, "/api/parent/probe", token, name=STAFF_ACCESS.name))
    # 不存在的家長（簽章正確）
    ghost = create_access_token(
        subject_type="parent", subject_id=uuid4(), token_version=0, clock=fake_clock
    )
    _assert_401(_get(client, "/api/parent/probe", ghost))

    p.status = "disabled"
    db_session.commit()
    _assert_401(_get(client, "/api/parent/probe", token))

    p.status = "active"
    p.token_version = 1
    db_session.commit()
    _assert_401(_get(client, "/api/parent/probe", token))

    # 過期 token
    p.token_version = 0
    db_session.commit()
    assert _get(client, "/api/parent/probe", token).status_code == 200
    fake_clock.advance(minutes=15, seconds=1)
    _assert_401(_get(client, "/api/parent/probe", token))


def test_current_parent_no_children_allowed(
    client: TestClient, db_session: Session, fake_clock: FakeClock
) -> None:
    p = make_parent(db_session)
    db_session.commit()

    response = _get(client, "/api/parent/probe", _token(p, fake_clock))

    assert response.status_code == 200
    assert response.json()["id"] == str(p.id)


def test_optional_parent_none(
    client: TestClient, db_session: Session, fake_clock: FakeClock
) -> None:
    p = make_parent(db_session)
    db_session.commit()
    token = _token(p, fake_clock)

    assert _get(client, "/api/parent/optional", token).json() == str(p.id)
    assert _get(client, "/api/parent/optional", None).json() == "none"
    assert _get(client, "/api/parent/optional", "garbage").json() == "none"

    p.status = "disabled"
    db_session.commit()
    assert _get(client, "/api/parent/optional", token).json() == "none"


def test_current_parent_load_direct(db_session: Session, fake_clock: FakeClock) -> None:
    """WS 重用的核心函式：不經 Request 也能驗證；失敗拋 UnauthenticatedError。"""
    p = make_parent(db_session)

    current = load_current_parent(db_session, _token(p, fake_clock), clock=fake_clock)
    assert current == CurrentParent(
        id=p.id, line_user_id=p.line_user_id, display_name="王媽媽", token_version=0
    )

    with pytest.raises(AppError) as excinfo:
        load_current_parent(db_session, None, clock=fake_clock)
    assert excinfo.value.status == 401
    assert excinfo.value.code == "unauthenticated"
