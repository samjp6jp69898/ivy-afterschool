"""FakeLineVerifier 測試替身（BACKEND-051）。

與 ``app.services.auth.line_id_token.LineIdTokenVerifier`` 結構相容；app 層測試以
``app.dependency_overrides[get_line_verifier] = lambda: fake`` 注入，不連 LINE。
"""

from collections.abc import Mapping

from app.core.errors import AppError
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
