"""BACKEND-064：GET /api/parent/me。
BACKEND-053：POST /api/parent/auth/liff-login。"""

from __future__ import annotations

import json
from collections.abc import Callable

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models.account import StaffUser
from app.models.parents import ParentAccount
from app.services.auth.line_id_token import LineProfile, get_line_verifier
from app.services.auth.throttle import AuthThrottles, get_auth_throttles
from app.services.settings_service import invalidate_setting
from tests.support.factories import make_guardian, make_parent, make_student
from tests.support.fake_line import FakeLineVerifier

_URL = "/api/parent/me"
ParentClientFactory = Callable[..., tuple[TestClient, ParentAccount]]
StaffClientFactory = Callable[..., tuple[TestClient, StaffUser]]
AssertError = Callable[..., None]


def test_parent_me_success(parent_client: ParentClientFactory, db_session: Session) -> None:
    client, parent = parent_client(display_name="王媽媽")
    ming = make_student(db_session, name="王小明", grade_level=3)
    make_guardian(db_session, ming, parent=parent)
    db_session.commit()

    resp = client.get(_URL)

    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == str(parent.id)
    assert body["display_name"] == "王媽媽"
    assert set(body) == {"id", "display_name", "picture_url", "phone", "children"}
    assert len(body["children"]) == 1
    child = body["children"][0]
    assert child["name"] == "王小明"
    assert child["id"] == str(ming.id)
    assert child["grade_level"] == 3
    # 家長端不輸出敏感欄位
    assert set(child) == {
        "id",
        "name",
        "grade_level",
        "class_name",
        "school_name",
        "photo_url",
        "status",
    }


def test_parent_me_401(
    api_client: TestClient,
    staff_client: StaffClientFactory,
    assert_error: AssertError,
) -> None:
    assert_error(api_client.get(_URL), 401, "unauthenticated")
    staff, _ = staff_client(permissions=["students:read"])
    assert_error(staff.get(_URL), 401, "unauthenticated")


def test_parent_me_disabled_401(
    parent_client: ParentClientFactory, assert_error: AssertError
) -> None:
    client, _ = parent_client(status="disabled")

    assert_error(client.get(_URL), 401, "unauthenticated")


def test_parent_me_idor_isolation(parent_client: ParentClientFactory, db_session: Session) -> None:
    client_a, parent_a = parent_client(display_name="王媽媽")
    client_b, parent_b = parent_client(display_name="陳媽媽")
    ming = make_student(db_session, name="王小明")
    hua = make_student(db_session, name="陳小華")
    shared = make_student(db_session, name="共同小孩")
    make_guardian(db_session, ming, parent=parent_a)
    make_guardian(db_session, hua, parent=parent_b)
    make_guardian(db_session, shared, parent=parent_a)
    make_guardian(db_session, shared, parent=parent_b, name="王爸爸", relation="father")
    db_session.commit()

    names_a = {c["name"] for c in client_a.get(_URL).json()["children"]}
    names_b = {c["name"] for c in client_b.get(_URL).json()["children"]}

    assert names_a == {"王小明", "共同小孩"}
    assert names_b == {"陳小華", "共同小孩"}
    body_a = client_a.get(_URL).json()
    assert body_a["id"] == str(parent_a.id)
    assert "陳媽媽" not in client_a.get(_URL).text


def test_parent_me_no_children(parent_client: ParentClientFactory) -> None:
    client, _ = parent_client(display_name="王媽媽")

    resp = client.get(_URL)

    assert resp.status_code == 200
    assert resp.json()["children"] == []
    assert resp.json()["display_name"] == "王媽媽"


def test_parent_me_archived_links_hidden(
    parent_client: ParentClientFactory, db_session: Session
) -> None:
    client, parent = parent_client()
    make_guardian(
        db_session, make_student(db_session, name="已封存學生", archived=True), parent=parent
    )
    make_guardian(
        db_session, make_student(db_session, name="封存監護"), parent=parent, archived=True
    )
    db_session.commit()

    assert client.get(_URL).json()["children"] == []


# --- BACKEND-053：POST /api/parent/auth/liff-login ---------------------------------------------

_LIFF_LOGIN = "/api/parent/auth/liff-login"
_LINE_A = "U" + "a" * 32
_LINE_B = "U" + "b" * 32
_ID_TOKEN = "tok"  # noqa: S105  測試假值


@pytest.fixture
def liff_configured(db_session: Session) -> None:
    db_session.execute(
        text("update public.system_settings set value = cast(:v as jsonb) where key = 'line.liff'"),
        {
            "v": json.dumps(
                {"liff_id": "1657000000-Abc", "channel_id": "1657000000", "add_friend_url": ""}
            )
        },
    )
    db_session.commit()
    invalidate_setting("line.liff")


@pytest.fixture
def line_verifier(app: FastAPI) -> FakeLineVerifier:
    verifier = FakeLineVerifier(
        {
            _ID_TOKEN: LineProfile(_LINE_A, "王媽媽", None),
            "tok-unknown": LineProfile(_LINE_B, "王媽媽", "https://profile.line-scdn.net/p"),
        }
    )
    app.dependency_overrides[get_line_verifier] = lambda: verifier
    return verifier


@pytest.fixture
def parent_throttles(app: FastAPI) -> AuthThrottles:
    instance = AuthThrottles()
    app.dependency_overrides[get_auth_throttles] = lambda: instance
    return instance


@pytest.mark.usefixtures("liff_configured", "line_verifier", "parent_throttles")
def test_parent_liff_login_ok(api_client: TestClient, db_session: Session) -> None:
    parent = make_parent(db_session, line_user_id=_LINE_A, display_name="舊暱稱")
    make_guardian(db_session, make_student(db_session, name="王小明"), parent=parent)
    db_session.commit()

    resp = api_client.post(_LIFF_LOGIN, json={"id_token": _ID_TOKEN})

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["name_hint"] is None
    assert body["parent"]["id"] == str(parent.id)
    assert body["parent"]["display_name"] == "王媽媽"
    assert [c["name"] for c in body["parent"]["children"]] == ["王小明"]
    assert resp.cookies.get("parent_access")
    assert resp.cookies.get("parent_refresh")
    assert not resp.cookies.get("parent_bind")
    assert "token" not in body
    assert resp.cookies.get("parent_access") not in resp.text
    # 已 commit：之後用 cookie 打 /me 成功；DB 的 last_login_at 已更新
    assert api_client.get("/api/parent/me").status_code == 200
    db_session.expire_all()
    assert parent.last_login_at is not None


@pytest.mark.usefixtures("liff_configured", "line_verifier", "parent_throttles")
def test_parent_liff_login_needs_binding(api_client: TestClient, db_session: Session) -> None:
    resp = api_client.post(_LIFF_LOGIN, json={"id_token": "tok-unknown"})

    assert resp.status_code == 200
    assert resp.json() == {"status": "needs_binding", "parent": None, "name_hint": "王媽媽"}
    bind = resp.cookies.get("parent_bind")
    assert bind
    assert not resp.cookies.get("parent_access")
    assert not resp.cookies.get("parent_refresh")
    assert bind not in resp.text
    # 沒有建立家長帳號
    assert (
        db_session.execute(
            select(ParentAccount).where(ParentAccount.line_user_id == _LINE_B)
        ).first()
        is None
    )
    # 仍未登入
    assert api_client.get("/api/parent/me").status_code == 401


@pytest.mark.usefixtures("liff_configured", "line_verifier", "parent_throttles")
def test_parent_liff_login_422(api_client: TestClient, assert_error: AssertError) -> None:
    assert_error(api_client.post(_LIFF_LOGIN, json={}), 422, "validation_error")
    assert_error(
        api_client.post(_LIFF_LOGIN, json={"id_token": "x", "extra": 1}), 422, "validation_error"
    )
    assert_error(api_client.post(_LIFF_LOGIN, json={"id_token": ""}), 422, "validation_error")


@pytest.mark.usefixtures("liff_configured", "parent_throttles")
def test_parent_liff_login_401(
    api_client: TestClient, app: FastAPI, assert_error: AssertError
) -> None:
    verifier = FakeLineVerifier(
        error=AppError("invalid_id_token", "LINE 登入驗證失敗，請重新開啟", status=401)
    )
    app.dependency_overrides[get_line_verifier] = lambda: verifier

    resp = api_client.post(_LIFF_LOGIN, json={"id_token": "bad"})

    assert_error(resp, 401, "invalid_id_token")
    assert not resp.cookies.get("parent_access")
    assert not resp.cookies.get("parent_bind")
    assert verifier.calls == [("bad", "1657000000")]


@pytest.mark.usefixtures("liff_configured", "line_verifier", "parent_throttles")
def test_parent_liff_login_403_disabled(
    api_client: TestClient, db_session: Session, assert_error: AssertError
) -> None:
    parent = make_parent(db_session, line_user_id=_LINE_A, status="disabled")
    make_guardian(db_session, make_student(db_session), parent=parent)
    db_session.commit()

    resp = api_client.post(_LIFF_LOGIN, json={"id_token": _ID_TOKEN})
    assert_error(resp, 403, "parent_disabled")
    assert not resp.cookies.get("parent_access")

    foreign = api_client.post(
        _LIFF_LOGIN, json={"id_token": _ID_TOKEN}, headers={"Origin": "https://evil.test"}
    )
    assert_error(foreign, 403, "origin_forbidden")


@pytest.mark.usefixtures("line_verifier", "parent_throttles")
def test_parent_liff_login_503_not_configured(
    api_client: TestClient, db_session: Session, assert_error: AssertError
) -> None:
    db_session.execute(
        text("update public.system_settings set value = cast(:v as jsonb) where key = 'line.liff'"),
        {"v": json.dumps({"liff_id": "", "channel_id": "", "add_friend_url": ""})},
    )
    db_session.commit()
    invalidate_setting("line.liff")

    assert_error(
        api_client.post(_LIFF_LOGIN, json={"id_token": _ID_TOKEN}),
        503,
        "line_login_not_configured",
    )


def test_parent_liff_login_route_registered(app: FastAPI) -> None:
    assert "post" in app.openapi()["paths"][_LIFF_LOGIN]
