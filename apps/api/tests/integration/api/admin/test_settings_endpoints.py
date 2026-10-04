"""BACKEND-112：GET /api/admin/settings。
BACKEND-113：PUT /api/admin/settings/{key}。"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx2
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
    assert messaging["value"]["channel_access_token"] == "****9999"  # noqa: S105
    assert messaging["value"]["channel_secret"] == "****3456"  # noqa: S105


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


# --- BACKEND-113：PUT /api/admin/settings/{key} ------------------------------------------------

_LIFF_VALUE = {"liff_id": "1657000000-AbcdEfgh", "channel_id": "1657000000"}


def _put(client: TestClient, key: str, value: dict[str, Any]) -> httpx2.Response:
    return client.put(f"{_URL}/{key}", json={"value": value})


def test_admin_settings_put_success(
    staff_client: StaffClientFactory, api_client: TestClient, db_session: Session
) -> None:
    client, staff = staff_client(permissions=["settings:write"], display_name="陳主任")

    resp = _put(client, "line.liff", _LIFF_VALUE)

    assert resp.status_code == 200
    body = resp.json()
    assert body["key"] == "line.liff"
    assert body["value"]["liff_id"] == "1657000000-AbcdEfgh"
    assert body["value"]["channel_id"] == "1657000000"
    assert body["value"]["add_friend_url"] == ""
    assert body["updated_by_name"] == "陳主任"
    assert body["updated_at"] is not None
    assert set(body) == {
        "key",
        "group",
        "label",
        "is_secret",
        "value",
        "json_schema",
        "updated_at",
        "updated_by_name",
    }
    # 已 commit 且快取已失效：公開設定立即反映
    assert api_client.get("/api/parent/config").json()["liff_id"] == "1657000000-AbcdEfgh"
    row = db_session.execute(
        text("select value, updated_by from public.system_settings where key = 'line.liff'")
    ).one()
    assert row.value["liff_id"] == "1657000000-AbcdEfgh"
    assert row.updated_by == staff.id
    # 寫 audit
    assert (
        db_session.execute(
            text(
                "select count(*) from public.audit_logs "
                "where action = 'settings.update' and entity_id = 'line.liff'"
            )
        ).scalar_one()
        == 1
    )


def test_admin_settings_put_secret_masked_in_response(staff_client: StaffClientFactory) -> None:
    client, _ = staff_client(permissions=["settings:write"])

    resp = _put(
        client,
        "line.messaging",
        {"channel_access_token": _TOKEN, "channel_secret": _SECRET},
    )

    assert resp.status_code == 200
    assert _TOKEN not in resp.text
    assert _SECRET not in resp.text
    assert resp.json()["value"]["channel_access_token"] == "****9999"  # noqa: S105


def test_admin_settings_put_422(
    staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    client, _ = staff_client(permissions=["settings:write"])

    invalid = _put(client, "line.liff", {"liff_id": "bad id", "channel_id": "1657000000"})
    assert_error(invalid, 422, "invalid_setting_value")
    assert invalid.json()["error"]["details"][0]["loc"] == ["liff_id"]
    assert "bad id" not in invalid.text

    extra = client.put(f"{_URL}/line.liff", json={"value": {}, "x": 1})
    assert_error(extra, 422, "validation_error")
    missing = client.put(f"{_URL}/line.liff", json={})
    assert_error(missing, 422, "validation_error")
    not_object = client.put(f"{_URL}/line.liff", json={"value": "x"})
    assert_error(not_object, 422, "validation_error")


def test_admin_settings_put_401(api_client: TestClient, assert_error: AssertError) -> None:
    assert_error(_put(api_client, "line.liff", _LIFF_VALUE), 401, "unauthenticated")


def test_admin_settings_put_403(
    staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    client, _ = staff_client(permissions=["settings:read"])

    resp = _put(client, "line.liff", _LIFF_VALUE)

    assert_error(resp, 403, "permission_denied")
    assert resp.json()["error"]["details"] == {"required": ["settings:write"]}
    foreign = client.put(
        f"{_URL}/line.liff", json={"value": _LIFF_VALUE}, headers={"Origin": "https://evil.test"}
    )
    assert_error(foreign, 403, "origin_forbidden")


def test_admin_settings_put_404(
    staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    client, _ = staff_client(permissions=["settings:write"])

    assert_error(_put(client, "foo.bar", {}), 404, "setting_not_found")
