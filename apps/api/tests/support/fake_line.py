"""FakeLineVerifier 測試替身（BACKEND-051）與 FakeLineMessagingClient（BACKEND-207 / 208）。

與 ``app.services.auth.line_id_token.LineIdTokenVerifier`` /
``app.notifications.channels.line.LineMessagingClient`` 結構相容；app 層測試以
``app.dependency_overrides[get_line_verifier] = lambda: fake`` 注入，不連 LINE。
"""

import threading
from collections.abc import Callable, Mapping
from uuid import UUID

from app.core.errors import AppError
from app.notifications.channels.line import LineSendResult
from app.services.auth.line_id_token import LineProfile


class FakeLineVerifier:
    def __init__(
        self,
        profiles: Mapping[str, LineProfile] | None = None,
        *,
        error: AppError | None = None,
    ) -> None:
        """profiles：id_token → 驗證成功時回傳的 LineProfile；error：設定後每次 verify 都拋它。"""
        self.profiles = dict(profiles or {})
        self.error = error
        self.calls: list[tuple[str, str]] = []

    def verify(self, id_token: str, *, channel_id: str) -> LineProfile:
        self.calls.append((id_token, channel_id))
        if self.error is not None:
            raise self.error
        profile = self.profiles.get(id_token)
        if profile is None:
            raise AppError("invalid_id_token", "LINE 登入驗證失敗，請重新開啟", status=401)
        return profile


class FakeLineMessagingClient:
    """與 ``app.notifications.channels.line.LineMessagingClient`` 結構相容（BACKEND-208 dispatcher
    測試用）。``result`` 為每次 push 的固定結果；``before_return`` 可注入阻塞（並發測試用）。
    執行緒安全：``calls`` 的寫入在鎖內。"""

    def __init__(
        self,
        result: LineSendResult | None = None,
        *,
        before_return: Callable[[], None] | None = None,
    ) -> None:
        self.result = result if result is not None else LineSendResult(True, False, None)
        self.before_return = before_return
        self.calls: list[tuple[str, str, UUID]] = []
        self._lock = threading.Lock()

    def push_text(self, *, to: str, text: str, retry_key: UUID) -> LineSendResult:
        with self._lock:
            self.calls.append((to, text, retry_key))
        if self.before_return is not None:
            self.before_return()
        return self.result
