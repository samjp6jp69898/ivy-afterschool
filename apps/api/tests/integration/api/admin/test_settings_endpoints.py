"""BACKEND-112：GET /api/admin/settings。"""

from __future__ import annotations

import json
from collections.abc import Callable

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.crypto import encrypt_token
from app.core.settings_registry import REGISTRY
from app.models.account import StaffUser
from tests.support.factories import make_staff
from tests.support.route_audit import admin_routes_without_permission

_URL = "/api/admin/settings"
StaffClientFactory = Callable[..., tuple[TestClient, StaffUser]]
AssertError = Callable[..., None]
_TOKEN = "tok-abcdefgh9999"  # noqa: S105  測試假值
_SECRET = "secret-value-123456"  # noqa: S105  測試假值


def _store_messaging(db: Session) -> None:
    db.execute(
        text("update public.system_settings set value = cast(:v as jsonb) where key = :k"),
        {
            "v": json.dumps(
                {
                    "channel_access_token": encrypt_token(_TOKEN),
                    "channel_secret": encrypt_token(_SECRET),
                }
            ),
            "k": "line.messaging",
        },
    )


def test_admin_settings_list_success(staff_client: StaffClientFactory) -> None:
    client, _ = staff_client(permissions=["settings:read"])

    resp = client.get(_URL)

    assert resp.status_code == 200
    items = resp.json()["items"]
    assert [i["key"] for i in items] == list(REGISTRY)
    assert len(items) == 11
    org = next(i for i in items if i["key"] == "org.profile")
    assert org["label"] == "安親班資料"
    assert org["group"] == "org"
    assert {"name", "logo_url"} <= set(org["json_schema"]["properties"])
    assert set(org) == {
        "key",
        "group",
        "label",
        "is_secret",
        "value",
        "json_schema",
        "updated_at",
        "updated_by_name",
    }


def test_admin_settings_list_masked(staff_client: StaffClientFactory, db_session: Session) -> None:
    _store_messaging(db_session)
    client, _ = staff_client(permissions=["settings:read"])

    resp = client.get(_URL)

    assert resp.status_code == 200
    assert _TOKEN not in resp.text
    assert _SECRET not in resp.text
    assert "****9999" in resp.text
    messaging = next(i for i in resp.json()["items"] if i["key"] == "line.messaging")
    assert messaging["is_secret"] is True
    assert messaging["value"]["channel_access_token"] == "****9999"
    assert messaging["value"]["channel_secret"] == "****3456"


def test_admin_settings_list_401(api_client: TestClient, assert_error: AssertError) -> None:
    assert_error(api_client.get(_URL), 401, "unauthenticated")


def test_admin_settings_list_403(
    staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    client, _ = staff_client(permissions=["students:read"])

    resp = client.get(_URL)

    assert_error(resp, 403, "permission_denied")
    assert resp.json()["error"]["details"] == {"required": ["settings:read"]}


def test_admin_settings_list_tutor_forbidden(
    api_client: TestClient,
    db_session: Session,
    login_staff: Callable[[TestClient, StaffUser], None],
    assert_error: AssertError,
) -> None:
    tutor = make_staff(db_session, role_code="tutor")
    db_session.commit()
    login_staff(api_client, tutor)

    assert_error(api_client.get(_URL), 403, "permission_denied")


def test_admin_settings_list_guard_registered(app: FastAPI) -> None:
    assert admin_routes_without_permission(app) == []
    assert "/api/admin/settings" in app.openapi()["paths"]
