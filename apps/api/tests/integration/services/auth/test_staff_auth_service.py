"""BACKEND-041：app/services/auth/staff_auth.py（StaffAuthService.login）。

帳密驗證、(帳號, IP) 鎖定、不存在帳號仍跑一次 argon2（防帳號列舉）、停用帳號同一個 401、
成功後更新 last_login_at / 簽發 access + refresh token / needs_rehash 透明升級。
"""

from collections.abc import Iterator

import pytest
from argon2 import PasswordHasher
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.crypto import derive_key
from app.core.errors import AppError
from app.core.request_meta import RequestMeta
from app.core.security.passwords import needs_rehash
from app.core.security.tokens import decode_access_token
from app.models.account import RefreshToken, StaffUser
from app.services.auth import staff_auth
from app.services.auth.staff_auth import StaffSession, login
from app.services.auth.throttle import AuthThrottles
from tests.support.factories import make_staff
from tests.support.fake_clock import FakeClock

_PASSWORD = "Passw0rd-Test1"  # noqa: S105  測試固定密碼
_WRONG = "wrong"  # 測試用錯誤密碼
_META = RequestMeta(ip="203.0.113.5", user_agent="pytest", request_id=None)


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
def throttles() -> AuthThrottles:
    return AuthThrottles()


def _login(
    db_session: Session,
    throttles: AuthThrottles,
    fake_clock: FakeClock,
    *,
    username: str,
    password: str,
    meta: RequestMeta = _META,
) -> StaffSession:
    return login(
        db_session,
        username=username,
        password=password,
        meta=meta,
        throttles=throttles,
        clock=fake_clock,
    )


def _refresh_count(db_session: Session, staff: StaffUser) -> int:
    return int(
        db_session.execute(
            select(func.count())
            .select_from(RefreshToken)
            .where(RefreshToken.subject_type == "staff", RefreshToken.subject_id == staff.id)
        ).scalar_one()
    )


def _assert_invalid_credentials(exc: AppError) -> None:
    assert exc.status == 401
    assert exc.code == "invalid_credentials"
    assert exc.message == "帳號或密碼錯誤"


def test_login_success(
    db_session: Session, throttles: AuthThrottles, fake_clock: FakeClock
) -> None:
    staff = make_staff(db_session, username="lin.teacher", permissions=["students:read"])
    assert staff.last_login_at is None

    result = _login(db_session, throttles, fake_clock, username="Lin.Teacher ", password=_PASSWORD)

    assert isinstance(result, StaffSession)
    assert result.staff.id == staff.id
    assert result.permissions == frozenset({"students:read"})
    assert staff.last_login_at == fake_clock.now()
    assert _refresh_count(db_session, staff) == 1
    claims = decode_access_token(result.access_token, expected_type="staff", clock=fake_clock)
    assert claims.subject_id == staff.id
    assert claims.token_version == staff.token_version
    assert len(result.refresh_token) >= 64
    assert result.refresh_token != result.access_token


def test_login_wrong_password(
    db_session: Session, throttles: AuthThrottles, fake_clock: FakeClock
) -> None:
    staff = make_staff(db_session)

    with pytest.raises(AppError) as excinfo:
        _login(db_session, throttles, fake_clock, username=staff.username, password=_WRONG)

    _assert_invalid_credentials(excinfo.value)
    assert _refresh_count(db_session, staff) == 0
    assert staff.last_login_at is None


def test_login_unknown_user_same_error_and_dummy(
    db_session: Session,
    throttles: AuthThrottles,
    fake_clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(staff_auth, "dummy_verify", lambda plain: calls.append(plain))

    with pytest.raises(AppError) as excinfo:
        _login(db_session, throttles, fake_clock, username="ghost", password=_PASSWORD)

    _assert_invalid_credentials(excinfo.value)
    assert calls == [_PASSWORD]


def test_login_inactive_same_error(
    db_session: Session, throttles: AuthThrottles, fake_clock: FakeClock
) -> None:
    staff = make_staff(db_session, is_active=False)

    with pytest.raises(AppError) as excinfo:
        _login(db_session, throttles, fake_clock, username=staff.username, password=_PASSWORD)

    _assert_invalid_credentials(excinfo.value)
    assert _refresh_count(db_session, staff) == 0
    assert staff.last_login_at is None


def test_login_lockout_after_5_failures(
    db_session: Session, throttles: AuthThrottles, fake_clock: FakeClock
) -> None:
    staff = make_staff(db_session)

    for _ in range(5):
        with pytest.raises(AppError) as excinfo:
            _login(db_session, throttles, fake_clock, username=staff.username, password=_WRONG)
        _assert_invalid_credentials(excinfo.value)

    with pytest.raises(AppError) as locked:
        _login(db_session, throttles, fake_clock, username=staff.username, password=_PASSWORD)
    assert locked.value.status == 429
    assert locked.value.code == "too_many_attempts"
    assert _refresh_count(db_session, staff) == 0

    fake_clock.advance(minutes=15, seconds=1)
    result = _login(db_session, throttles, fake_clock, username=staff.username, password=_PASSWORD)
    assert result.staff.id == staff.id
    assert _refresh_count(db_session, staff) == 1


def test_login_lockout_is_per_account_and_ip(
    db_session: Session, throttles: AuthThrottles, fake_clock: FakeClock
) -> None:
    """鎖定以 (帳號, IP) 為單位：同帳號換 IP、同 IP 換帳號都不受影響。"""
    staff = make_staff(db_session)
    other = make_staff(db_session)
    for _ in range(5):
        with pytest.raises(AppError):
            _login(db_session, throttles, fake_clock, username=staff.username, password=_WRONG)

    other_ip = RequestMeta(ip="198.51.100.9", user_agent=None, request_id=None)
    assert (
        _login(
            db_session,
            throttles,
            fake_clock,
            username=staff.username,
            password=_PASSWORD,
            meta=other_ip,
        ).staff.id
        == staff.id
    )
    assert (
        _login(
            db_session, throttles, fake_clock, username=other.username, password=_PASSWORD
        ).staff.id
        == other.id
    )


def test_login_success_clears_failure_count(
    db_session: Session, throttles: AuthThrottles, fake_clock: FakeClock
) -> None:
    """成功登入清除失敗計數：4 次失敗 → 成功 → 再 4 次失敗仍不鎖。"""
    staff = make_staff(db_session)
    for _ in range(4):
        with pytest.raises(AppError):
            _login(db_session, throttles, fake_clock, username=staff.username, password=_WRONG)
    _login(db_session, throttles, fake_clock, username=staff.username, password=_PASSWORD)
    for _ in range(4):
        with pytest.raises(AppError) as excinfo:
            _login(db_session, throttles, fake_clock, username=staff.username, password=_WRONG)
        assert excinfo.value.status == 401
    assert (
        _login(
            db_session, throttles, fake_clock, username=staff.username, password=_PASSWORD
        ).staff.id
        == staff.id
    )


def test_login_rehash_weak_hash(
    db_session: Session, throttles: AuthThrottles, fake_clock: FakeClock
) -> None:
    staff = make_staff(db_session)
    weak = PasswordHasher(time_cost=1).hash(_PASSWORD)
    staff.password_hash = weak
    db_session.flush()
    assert needs_rehash(weak) is True

    _login(db_session, throttles, fake_clock, username=staff.username, password=_PASSWORD)

    assert staff.password_hash != weak
    assert staff.password_hash.startswith("$argon2id$")
    assert needs_rehash(staff.password_hash) is False


def test_login_does_not_commit(
    db_session: Session, throttles: AuthThrottles, fake_clock: FakeClock
) -> None:
    """service 只 flush：rollback 到 savepoint 後 refresh token 與 last_login_at 都退回。"""
    staff = make_staff(db_session)
    db_session.commit()
    _login(db_session, throttles, fake_clock, username=staff.username, password=_PASSWORD)
    assert _refresh_count(db_session, staff) == 1

    db_session.rollback()

    assert _refresh_count(db_session, staff) == 0
    assert (
        db_session.execute(
            select(StaffUser.last_login_at).where(StaffUser.id == staff.id)
        ).scalar_one()
        is None
    )
