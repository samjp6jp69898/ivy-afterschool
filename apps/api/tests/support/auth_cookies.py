"""BACKEND-023：endpoint 整合測試直接設定登入 cookie（不走 /login，避開節流與密碼雜湊成本）。

以 BACKEND-033 ``create_access_token`` 簽發 access token、用 BACKEND-035 的 cookie 名稱設進
``client.cookies``。token 的 ``tv`` 取自帳號目前的 ``token_version``，與 ``get_current_staff`` /
``get_current_parent`` 的檢查一致。只設 access cookie；需要 refresh cookie 的測試（refresh /
logout endpoint）自行以 ``refresh_tokens.issue`` 取得 raw 後設定。
"""

from __future__ import annotations

import httpx2
from fastapi.testclient import TestClient

from app.core.clock import Clock
from app.core.security.cookies import PARENT_ACCESS, STAFF_ACCESS
from app.core.security.tokens import create_access_token
from app.models.account import StaffUser
from app.models.parents import ParentAccount


def login_staff(client: TestClient, staff: StaffUser, *, clock: Clock) -> None:
    token = create_access_token(
        subject_type="staff", subject_id=staff.id, token_version=staff.token_version, clock=clock
    )
    client.cookies.set(STAFF_ACCESS.name, token)


def login_parent(client: TestClient, parent: ParentAccount, *, clock: Clock) -> None:
    token = create_access_token(
        subject_type="parent",
        subject_id=parent.id,
        token_version=parent.token_version,
        clock=clock,
    )
    client.cookies.set(PARENT_ACCESS.name, token)


def assert_error(response: httpx2.Response, status: int, code: str) -> None:
    """斷言錯誤 envelope（domain_spec §2）的 status 與 ``error.code``。"""
    assert response.status_code == status, (
        f"預期 {status}，實際 {response.status_code}：{response.text}"
    )
    assert response.json()["error"]["code"] == code, response.text
