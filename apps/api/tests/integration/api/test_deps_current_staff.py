"""BACKEND-047：app/api/deps.py 員工守衛（get_current_staff / load_current_staff）。

迷你 app：get_db → override_get_db(db_session)、get_clock → fake_clock；探針路由回傳守衛結果。
測資先 commit（釋放 savepoint）再打 API，兩次請求之間改的資料同樣要 commit。
"""

from collections.abc import Iterator
from typing import Annotated
from uuid import uuid4

import httpx
import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.deps import PASSWORD_CHANGE_ALLOWED_PATHS, CurrentStaff, get_current_staff
from app.core.clock import get_clock
from app.core.config import get_settings
from app.core.crypto import derive_key
from app.core.db import get_db
from app.core.errors import register_exception_handlers
from app.core.permissions import Permission
from app.core.security.cookies import STAFF_ACCESS
from app.core.security.tokens import SubjectType, create_access_token
from app.models.account import StaffUser
from tests.support.db_override import override_get_db
from tests.support.factories import make_staff
from tests.support.fake_clock import FakeClock

Staff = Annotated[CurrentStaff, Depends(get_current_staff)]


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

    @app.get("/api/admin/probe")
    def probe(request: Request, staff: Staff) -> dict:
        return {
            "permissions": sorted(staff.permissions),
            "username": staff.username,
            "role_code": staff.role_code,
            "must_change_password": staff.must_change_password,
            "has_students_read": staff.has(Permission.STUDENTS_READ),
            "state_is_same": request.state.current_staff is staff,
        }

    @app.get("/api/admin/auth/me")
    def me_probe(staff: Staff) -> dict:
        return {"id": str(staff.id)}

    with TestClient(app) as c:
        yield c


def _token(staff: StaffUser, fake_clock: FakeClock, *, subject_type: SubjectType = "staff") -> str:
    return create_access_token(
        subject_type=subject_type,
        subject_id=staff.id,
        token_version=staff.token_version,
        clock=fake_clock,
    )


def _assert_401(response: httpx.Response) -> None:
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"


def test_current_staff_ok(client: TestClient, db_session: Session, fake_clock: FakeClock) -> None:
    staff = make_staff(db_session, permissions=["students:read", "pickup:read"])
    db_session.commit()

    response = client.get(
        "/api/admin/probe", cookies={STAFF_ACCESS.name: _token(staff, fake_clock)}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["permissions"] == ["pickup:read", "students:read"]
    assert body["username"] == staff.username
    assert body["role_code"] == staff.role.code
    assert body["must_change_password"] is False
    assert body["has_students_read"] is True
    assert body["state_is_same"] is True


def test_current_staff_missing_or_bad_token(
    client: TestClient, db_session: Session, fake_clock: FakeClock
) -> None:
    staff = make_staff(db_session, permissions=["students:read"])
    db_session.commit()

    _assert_401(client.get("/api/admin/probe"))
    _assert_401(client.get("/api/admin/probe", cookies={STAFF_ACCESS.name: "garbage"}))
    parent_token = _token(staff, fake_clock, subject_type="parent")
    _assert_401(client.get("/api/admin/probe", cookies={STAFF_ACCESS.name: parent_token}))
    # 正確 token 放錯 cookie 名稱（家長 cookie）也不算登入
    good = _token(staff, fake_clock)
    _assert_401(client.get("/api/admin/probe", cookies={"parent_access": good}))
    # 過期 token
    fake_clock.advance(minutes=15, seconds=1)
    _assert_401(client.get("/api/admin/probe", cookies={STAFF_ACCESS.name: good}))


def test_current_staff_inactive_or_version_mismatch(
    client: TestClient, db_session: Session, fake_clock: FakeClock
) -> None:
    staff = make_staff(db_session, permissions=["students:read"])
    db_session.commit()
    token = _token(staff, fake_clock)
    assert client.get("/api/admin/probe", cookies={STAFF_ACCESS.name: token}).status_code == 200

    staff.is_active = False
    db_session.commit()
    _assert_401(client.get("/api/admin/probe", cookies={STAFF_ACCESS.name: token}))

    staff.is_active = True
    staff.token_version = 1
    db_session.commit()
    _assert_401(client.get("/api/admin/probe", cookies={STAFF_ACCESS.name: token}))

    # 不存在的帳號（簽章正確）
    ghost = create_access_token(
        subject_type="staff", subject_id=uuid4(), token_version=0, clock=fake_clock
    )
    _assert_401(client.get("/api/admin/probe", cookies={STAFF_ACCESS.name: ghost}))


def test_current_staff_must_change_password(
    client: TestClient, db_session: Session, fake_clock: FakeClock
) -> None:
    staff = make_staff(db_session, permissions=["students:read"], must_change_password=True)
    db_session.commit()
    token = _token(staff, fake_clock)

    response = client.get("/api/admin/probe", cookies={STAFF_ACCESS.name: token})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "password_change_required"

    allowed = client.get("/api/admin/auth/me", cookies={STAFF_ACCESS.name: token})
    assert allowed.status_code == 200
    assert allowed.json() == {"id": str(staff.id)}
    expected_paths = {
        "/api/admin/auth/me",
        "/api/admin/auth/change-password",
        "/api/admin/auth/logout",
    }
    assert set(PASSWORD_CHANGE_ALLOWED_PATHS) == expected_paths


def test_current_staff_permissions_live(
    client: TestClient, db_session: Session, fake_clock: FakeClock
) -> None:
    staff = make_staff(
        db_session,
        permissions=["students:read"],
        extra_permissions=["pickup:read"],
        revoked_permissions=["exams:write"],
    )
    db_session.commit()
    token = _token(staff, fake_clock)

    first = client.get("/api/admin/probe", cookies={STAFF_ACCESS.name: token})
    assert first.json()["permissions"] == ["pickup:read", "students:read"]

    staff.role.permissions = ["exams:read", "exams:write"]
    db_session.commit()

    second = client.get("/api/admin/probe", cookies={STAFF_ACCESS.name: token})
    assert second.json()["permissions"] == ["exams:read", "pickup:read"]
