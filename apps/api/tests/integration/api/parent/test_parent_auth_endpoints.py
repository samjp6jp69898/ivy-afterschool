"""BACKEND-064：GET /api/parent/me。"""

from __future__ import annotations

from collections.abc import Callable

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.account import StaffUser
from app.models.parents import ParentAccount
from tests.support.factories import make_guardian, make_student

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
