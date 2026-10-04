"""BACKEND-052：app/services/auth/parent_auth.py（ParentAuthService.liff_login）。

已綁定家長直接登入（同步暱稱、last_login_at、簽 access + refresh）；未建帳號或沒有任何有效綁定
→ NeedsBinding（發綁定臨時 token，不建立 parent_accounts）；停用 403；LIFF 未設定 503。
"""

# ruff: noqa: S105, S106  測試用的 token 字面值

import json
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.crypto import derive_key
from app.core.errors import AppError
from app.core.request_meta import RequestMeta
from app.core.security.tokens import decode_access_token, decode_bind_token
from app.models.account import RefreshToken
from app.models.parents import ParentAccount
from app.services.auth.line_id_token import LineProfile
from app.services.auth.parent_auth import NeedsBinding, ParentSession, liff_login
from app.services.auth.throttle import AuthThrottles
from app.services.settings_service import clear_settings_cache, invalidate_setting
from tests.support.factories import make_guardian, make_parent, make_student
from tests.support.fake_clock import FakeClock
from tests.support.fake_line import FakeLineVerifier

_LINE_A = "U" + "a" * 32
_LINE_B = "U" + "b" * 32
_ID_TOKEN = "id-token-1"
_META = RequestMeta(ip="203.0.113.5", user_agent="pytest", request_id=None)
_LIFF = {"liff_id": "1657000000-Abc", "channel_id": "1657000000", "add_friend_url": ""}


@pytest.fixture(autouse=True)
def _env_and_cache(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
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
    clear_settings_cache()
    yield
    clear_settings_cache()
    get_settings.cache_clear()
    derive_key.cache_clear()


@pytest.fixture
def throttles() -> AuthThrottles:
    return AuthThrottles()


def _set_liff(session: Session, value: dict[str, Any]) -> None:
    session.execute(
        text("update public.system_settings set value = cast(:v as jsonb) where key = 'line.liff'"),
        {"v": json.dumps(value)},
    )
    invalidate_setting("line.liff")


@pytest.fixture
def liff_configured(db_session: Session) -> None:
    _set_liff(db_session, _LIFF)


def _verifier(profile: LineProfile) -> FakeLineVerifier:
    return FakeLineVerifier({_ID_TOKEN: profile})


def _login(
    db_session: Session,
    verifier: FakeLineVerifier,
    throttles: AuthThrottles,
    fake_clock: FakeClock,
    *,
    id_token: str = _ID_TOKEN,
) -> ParentSession | NeedsBinding:
    return liff_login(
        db_session,
        id_token=id_token,
        verifier=verifier,
        meta=_META,
        throttles=throttles,
        clock=fake_clock,
    )


def _parent_count(db_session: Session) -> int:
    return int(db_session.execute(select(func.count()).select_from(ParentAccount)).scalar_one())


def _refresh_count(db_session: Session, parent: ParentAccount) -> int:
    return int(
        db_session.execute(
            select(func.count())
            .select_from(RefreshToken)
            .where(RefreshToken.subject_type == "parent", RefreshToken.subject_id == parent.id)
        ).scalar_one()
    )


@pytest.mark.usefixtures("liff_configured")
def test_liff_login_bound_parent(
    db_session: Session, throttles: AuthThrottles, fake_clock: FakeClock
) -> None:
    p = make_parent(db_session, line_user_id=_LINE_A, display_name="舊暱稱")
    make_guardian(db_session, make_student(db_session), parent=p)
    verifier = _verifier(LineProfile(_LINE_A, "王媽媽", None))
    count_before = _parent_count(db_session)

    result = _login(db_session, verifier, throttles, fake_clock)

    assert isinstance(result, ParentSession)
    assert result.parent.id == p.id
    assert p.display_name == "王媽媽"
    assert p.picture_url is None
    assert p.last_login_at == fake_clock.now()
    assert _refresh_count(db_session, p) == 1
    assert _parent_count(db_session) == count_before
    claims = decode_access_token(result.access_token, expected_type="parent", clock=fake_clock)
    assert claims.subject_id == p.id
    assert claims.token_version == p.token_version
    assert len(result.refresh_token) >= 64
    assert verifier.calls == [(_ID_TOKEN, "1657000000")]


@pytest.mark.usefixtures("liff_configured")
def test_liff_login_profile_sync_rules(
    db_session: Session, throttles: AuthThrottles, fake_clock: FakeClock
) -> None:
    """LINE 值非空且有變才更新；LINE 沒給暱稱時保留原值。"""
    p = make_parent(db_session, line_user_id=_LINE_A, display_name="王媽媽")
    p.picture_url = "https://profile.line-scdn.net/old"
    make_guardian(db_session, make_student(db_session), parent=p)

    _login(db_session, _verifier(LineProfile(_LINE_A, None, None)), throttles, fake_clock)
    assert p.display_name == "王媽媽"
    assert p.picture_url == "https://profile.line-scdn.net/old"

    _login(
        db_session,
        _verifier(LineProfile(_LINE_A, "王媽咪", "https://profile.line-scdn.net/new")),
        throttles,
        fake_clock,
    )
    assert p.display_name == "王媽咪"
    assert p.picture_url == "https://profile.line-scdn.net/new"
    # 兩次登入各自一個新 family
    assert _refresh_count(db_session, p) == 2


@pytest.mark.usefixtures("liff_configured")
def test_liff_login_unknown_needs_binding(
    db_session: Session, throttles: AuthThrottles, fake_clock: FakeClock
) -> None:
    verifier = _verifier(LineProfile(_LINE_B, "王媽媽", "https://profile.line-scdn.net/p"))
    count_before = _parent_count(db_session)

    result = _login(db_session, verifier, throttles, fake_clock)

    assert isinstance(result, NeedsBinding)
    assert result.name_hint == "王媽媽"
    claims = decode_bind_token(result.bind_token, clock=fake_clock)
    assert claims.line_user_id == _LINE_B
    assert claims.display_name == "王媽媽"
    assert claims.picture_url == "https://profile.line-scdn.net/p"
    assert _parent_count(db_session) == count_before
    assert (
        db_session.execute(
            select(func.count())
            .select_from(RefreshToken)
            .where(RefreshToken.subject_type == "parent")
        ).scalar_one()
        == 0
    )


@pytest.mark.usefixtures("liff_configured")
def test_liff_login_no_active_binding_needs_binding(
    db_session: Session, throttles: AuthThrottles, fake_clock: FakeClock
) -> None:
    p = make_parent(db_session, line_user_id=_LINE_A, display_name="王媽媽")
    make_guardian(db_session, make_student(db_session), parent=p, archived=True)
    count_before = _parent_count(db_session)

    result = _login(
        db_session, _verifier(LineProfile(_LINE_A, "王媽媽", None)), throttles, fake_clock
    )

    assert isinstance(result, NeedsBinding)
    assert result.name_hint == "王媽媽"
    assert decode_bind_token(result.bind_token, clock=fake_clock).line_user_id == _LINE_A
    assert _parent_count(db_session) == count_before
    assert _refresh_count(db_session, p) == 0
    assert p.last_login_at is None

    # 學生封存同樣不算有效綁定
    q = make_parent(db_session, line_user_id=_LINE_B)
    make_guardian(db_session, make_student(db_session, archived=True), parent=q)
    assert isinstance(
        _login(db_session, _verifier(LineProfile(_LINE_B, None, None)), throttles, fake_clock),
        NeedsBinding,
    )


@pytest.mark.usefixtures("liff_configured")
def test_liff_login_disabled_parent(
    db_session: Session, throttles: AuthThrottles, fake_clock: FakeClock
) -> None:
    p = make_parent(db_session, line_user_id=_LINE_A, status="disabled")
    make_guardian(db_session, make_student(db_session), parent=p)

    with pytest.raises(AppError) as excinfo:
        _login(db_session, _verifier(LineProfile(_LINE_A, "王媽媽", None)), throttles, fake_clock)

    assert excinfo.value.status == 403
    assert excinfo.value.code == "parent_disabled"
    assert _refresh_count(db_session, p) == 0
    assert p.last_login_at is None


def test_liff_login_not_configured(
    db_session: Session, throttles: AuthThrottles, fake_clock: FakeClock
) -> None:
    _set_liff(db_session, {**_LIFF, "channel_id": ""})
    verifier = _verifier(LineProfile(_LINE_A, "王媽媽", None))

    with pytest.raises(AppError) as excinfo:
        _login(db_session, verifier, throttles, fake_clock)

    assert excinfo.value.status == 503
    assert excinfo.value.code == "line_login_not_configured"
    assert verifier.calls == []


@pytest.mark.usefixtures("liff_configured")
def test_liff_login_verifier_errors_propagate(
    db_session: Session, throttles: AuthThrottles, fake_clock: FakeClock
) -> None:
    for error in (
        AppError("invalid_id_token", "LINE 登入驗證失敗，請重新開啟", status=401),
        AppError("line_unavailable", "LINE 驗證服務暫時無法連線", status=503),
    ):
        with pytest.raises(AppError) as excinfo:
            _login(db_session, FakeLineVerifier(error=error), throttles, fake_clock)
        assert excinfo.value is error

    # 未知的 id_token（FakeLineVerifier 回 401）
    with pytest.raises(AppError) as unknown:
        _login(
            db_session,
            _verifier(LineProfile(_LINE_A, None, None)),
            throttles,
            fake_clock,
            id_token="other",
        )
    assert unknown.value.code == "invalid_id_token"


@pytest.mark.usefixtures("liff_configured")
def test_liff_login_ip_rate_limited(
    db_session: Session, throttles: AuthThrottles, fake_clock: FakeClock
) -> None:
    """同 IP 5 分鐘 30 次（AuthThrottles.liff_ip）：第 31 次 429，且不呼叫 verifier。"""
    verifier = _verifier(LineProfile(_LINE_B, "王媽媽", None))
    for _ in range(30):
        _login(db_session, verifier, throttles, fake_clock)

    with pytest.raises(AppError) as excinfo:
        _login(db_session, verifier, throttles, fake_clock)
    assert excinfo.value.status == 429
    assert len(verifier.calls) == 30
