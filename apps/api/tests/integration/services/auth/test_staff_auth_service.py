"""BACKEND-041：app/services/auth/staff_auth.py（StaffAuthService.login）。
BACKEND-043：refresh（輪替 refresh 並以目前 token_version 重簽 access；停用帳號 / 家長 token 撤銷
family）。

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
from app.services.auth.refresh_tokens import hash_refresh, issue, revoke_family_by_raw
from app.services.auth.staff_auth import StaffSession, login, refresh
from app.services.auth.throttle import AuthThrottles
from tests.support.factories import make_parent, make_staff
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


# --- BACKEND-043：refresh ----------------------------------------------------------------------


def _family_rows(db_session: Session, raw: str) -> list[RefreshToken]:
    family_id = db_session.execute(
        select(RefreshToken.family_id).where(RefreshToken.token_hash == hash_refresh(raw))
    ).scalar_one()
    return list(
        db_session.execute(
            select(RefreshToken).where(RefreshToken.family_id == family_id)
        ).scalars()
    )


def _assert_family_revoked(db_session: Session, raw: str, *, revoked: bool) -> None:
    rows = _family_rows(db_session, raw)
    assert rows
    assert all((row.revoked_at is not None) is revoked for row in rows)


def test_refresh_success(
    db_session: Session, throttles: AuthThrottles, fake_clock: FakeClock
) -> None:
    staff = make_staff(db_session, permissions=["students:read"])
    s1 = _login(db_session, throttles, fake_clock, username=staff.username, password=_PASSWORD)
    fake_clock.advance(minutes=10)

    s2 = refresh(db_session, raw_refresh=s1.refresh_token, clock=fake_clock)

    assert s2.refresh_token != s1.refresh_token
    assert s2.access_token != s1.access_token
    assert s2.staff.id == staff.id
    assert s2.permissions == frozenset({"students:read"})
    claims = decode_access_token(s2.access_token, expected_type="staff", clock=fake_clock)
    assert claims.subject_id == staff.id
    assert claims.token_version == staff.token_version
    assert claims.issued_at == fake_clock.now()
    # 同一 family 輪替：舊列 replaced_by 指向新列，兩列都未撤銷
    rows = {row.token_hash: row for row in _family_rows(db_session, s1.refresh_token)}
    assert set(rows) == {hash_refresh(s1.refresh_token), hash_refresh(s2.refresh_token)}
    assert rows[hash_refresh(s1.refresh_token)].replaced_by == (
        rows[hash_refresh(s2.refresh_token)].id
    )
    assert all(row.revoked_at is None for row in rows.values())


def test_refresh_uses_current_token_version(
    db_session: Session, throttles: AuthThrottles, fake_clock: FakeClock
) -> None:
    """新 access 的 tv 取 DB 目前值（例如管理員重設密碼後 token_version 已 +1）。"""
    staff = make_staff(db_session)
    s1 = _login(db_session, throttles, fake_clock, username=staff.username, password=_PASSWORD)
    staff.token_version = 3
    db_session.flush()

    s2 = refresh(db_session, raw_refresh=s1.refresh_token, clock=fake_clock)

    claims = decode_access_token(s2.access_token, expected_type="staff", clock=fake_clock)
    assert claims.token_version == 3


def test_refresh_missing_cookie(db_session: Session, fake_clock: FakeClock) -> None:
    with pytest.raises(AppError) as excinfo:
        refresh(db_session, raw_refresh=None, clock=fake_clock)
    assert excinfo.value.status == 401
    assert excinfo.value.code == "unauthenticated"

    with pytest.raises(AppError) as empty:
        refresh(db_session, raw_refresh="", clock=fake_clock)
    assert empty.value.code == "unauthenticated"


def test_refresh_inactive_staff(
    db_session: Session, throttles: AuthThrottles, fake_clock: FakeClock
) -> None:
    staff = make_staff(db_session)
    s1 = _login(db_session, throttles, fake_clock, username=staff.username, password=_PASSWORD)
    staff.is_active = False
    db_session.flush()

    with pytest.raises(AppError) as excinfo:
        refresh(db_session, raw_refresh=s1.refresh_token, clock=fake_clock)

    assert excinfo.value.status == 401
    assert excinfo.value.code == "unauthenticated"
    _assert_family_revoked(db_session, s1.refresh_token, revoked=True)
    # 撤銷在 raise 前已 commit（釋放 savepoint）：endpoint 的 rollback 不會把撤銷退回
    db_session.rollback()
    _assert_family_revoked(db_session, s1.refresh_token, revoked=True)


def test_refresh_parent_token_rejected(db_session: Session, fake_clock: FakeClock) -> None:
    parent = make_parent(db_session)
    issued = issue(db_session, subject_type="parent", subject_id=parent.id, clock=fake_clock)

    with pytest.raises(AppError) as excinfo:
        refresh(db_session, raw_refresh=issued.raw, clock=fake_clock)

    assert excinfo.value.status == 401
    assert excinfo.value.code == "refresh_invalid"
    _assert_family_revoked(db_session, issued.raw, revoked=True)


def test_refresh_propagates_in_progress(
    db_session: Session, throttles: AuthThrottles, fake_clock: FakeClock
) -> None:
    staff = make_staff(db_session)
    s1 = _login(db_session, throttles, fake_clock, username=staff.username, password=_PASSWORD)
    refresh(db_session, raw_refresh=s1.refresh_token, clock=fake_clock)
    fake_clock.advance(seconds=2)

    with pytest.raises(AppError) as excinfo:
        refresh(db_session, raw_refresh=s1.refresh_token, clock=fake_clock)
    assert excinfo.value.status == 409
    assert excinfo.value.code == "refresh_in_progress"
    _assert_family_revoked(db_session, s1.refresh_token, revoked=False)

    # 超過容忍時間再用舊 token：重用 → 401 refresh_reused，整個 family 撤銷
    fake_clock.advance(seconds=10)
    with pytest.raises(AppError) as reused:
        refresh(db_session, raw_refresh=s1.refresh_token, clock=fake_clock)
    assert reused.value.status == 401
    assert reused.value.code == "refresh_reused"
    _assert_family_revoked(db_session, s1.refresh_token, revoked=True)


def test_refresh_propagates_revoked_and_invalid(
    db_session: Session, throttles: AuthThrottles, fake_clock: FakeClock
) -> None:
    staff = make_staff(db_session)
    s1 = _login(db_session, throttles, fake_clock, username=staff.username, password=_PASSWORD)
    revoke_family_by_raw(db_session, s1.refresh_token, clock=fake_clock)

    with pytest.raises(AppError) as revoked:
        refresh(db_session, raw_refresh=s1.refresh_token, clock=fake_clock)
    assert revoked.value.code == "refresh_revoked"

    with pytest.raises(AppError) as invalid:
        refresh(db_session, raw_refresh="no-such-token", clock=fake_clock)
    assert invalid.value.status == 401
    assert invalid.value.code == "refresh_invalid"
