"""BACKEND-040：認證相關節流（登入、改密碼、LIFF、綁定）。"""

import itertools
from collections.abc import Callable
from datetime import datetime

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.core.errors import RateLimitedError, register_exception_handlers
from app.core.rate_limit import FailureLockout, SlidingWindowLimiter
from app.services.auth.throttle import AuthThrottles, get_auth_throttles, login_keys
from tests.support.fake_clock import FakeClock


def _assert_hit_limit(
    hit: Callable[[datetime], None], now: datetime, allowed: int, retry: int
) -> None:
    for _ in range(allowed):
        hit(now)
    with pytest.raises(RateLimitedError) as exc_info:
        hit(now)
    assert exc_info.value.retry_after_seconds == retry


def _assert_lockout(lockout: FailureLockout, key: str, now: datetime, threshold: int) -> None:
    for _ in range(threshold - 1):
        lockout.record_failure(key, now)
    lockout.check(key, now)
    lockout.record_failure(key, now)
    with pytest.raises(RateLimitedError) as exc_info:
        lockout.check(key, now)
    assert exc_info.value.retry_after_seconds == 900


def test_auth_throttle_defaults(fake_clock: FakeClock) -> None:
    t = AuthThrottles()
    now = fake_clock.now()

    _assert_lockout(t.login_account_ip, "amy|1.2.3.4", now, threshold=5)
    _assert_hit_limit(lambda n: t.login_ip.hit("1.2.3.4", n), now, allowed=20, retry=300)


def test_auth_throttle_all_defaults(fake_clock: FakeClock) -> None:
    t = AuthThrottles()
    now = fake_clock.now()

    assert isinstance(t.login_ip, SlidingWindowLimiter)
    assert isinstance(t.liff_ip, SlidingWindowLimiter)
    _assert_hit_limit(lambda n: t.liff_ip.hit("1.2.3.4", n), now, allowed=30, retry=300)
    _assert_lockout(t.login_account, "amy", now, threshold=20)
    _assert_lockout(t.password_change, "staff-1", now, threshold=5)
    _assert_lockout(t.bind, "U" + "a" * 32, now, threshold=5)


def test_auth_throttle_windows_expire(fake_clock: FakeClock) -> None:
    t = AuthThrottles()
    for _ in range(20):
        t.login_ip.hit("1.2.3.4", fake_clock.now())
    for _ in range(5):
        t.login_account_ip.record_failure("amy|1.2.3.4", fake_clock.now())

    fake_clock.advance(seconds=300)
    t.login_ip.hit("1.2.3.4", fake_clock.now())
    with pytest.raises(RateLimitedError):
        t.login_account_ip.check("amy|1.2.3.4", fake_clock.now())

    fake_clock.advance(seconds=600)
    t.login_account_ip.check("amy|1.2.3.4", fake_clock.now())


def test_auth_throttle_instances_isolated(fake_clock: FakeClock) -> None:
    a, b = AuthThrottles(), AuthThrottles()
    now = fake_clock.now()

    for name in (
        "login_ip",
        "login_account_ip",
        "login_account",
        "password_change",
        "liff_ip",
        "bind",
    ):
        assert getattr(a, name) is not getattr(b, name), name

    for _ in range(5):
        a.login_account_ip.record_failure("amy|1.2.3.4", now)
    with pytest.raises(RateLimitedError):
        a.login_account_ip.check("amy|1.2.3.4", now)
    b.login_account_ip.check("amy|1.2.3.4", now)


def test_auth_throttle_singleton() -> None:
    assert get_auth_throttles() is get_auth_throttles()
    assert isinstance(get_auth_throttles(), AuthThrottles)


def test_auth_throttle_login_keys() -> None:
    assert login_keys("amy", None) == ("amy|-", "amy")
    assert login_keys("amy", "1.2.3.4") == ("amy|1.2.3.4", "amy")
    assert login_keys("amy", "2001:db8::1") == ("amy|2001:db8::1", "amy")
    # 空字串 IP 與 None 同樣視為未知來源
    assert login_keys("amy", "") == ("amy|-", "amy")


def test_auth_throttle_login_keys_normalize() -> None:
    assert login_keys(" Amy ", "1.2.3.4") == ("amy|1.2.3.4", "amy")
    assert login_keys("AMY", None) == login_keys("amy", None)
    assert login_keys("Lin.Teacher\t", "1.2.3.4") == login_keys("lin.teacher", "1.2.3.4")


def test_auth_throttle_login_keys_injective() -> None:
    assert login_keys("amy|1.2.3.4", None)[0] != login_keys("amy", "1.2.3.4")[0]
    assert login_keys("amy|-", None)[0] != login_keys("amy", None)[0]

    usernames = ["amy", "amy|1.2.3.4", "amy|-", "amy|", "|amy", "", "-", "1.2.3.4"]
    ips = [None, "1.2.3.4", "5.6.7.8", "2001:db8::1"]
    combos = list(itertools.product(usernames, ips))
    keys = [login_keys(u, ip)[0] for u, ip in combos]
    assert len(set(keys)) == len(combos)


def test_auth_throttle_rotating_ip_hits_account_lockout(fake_clock: FakeClock) -> None:
    t = AuthThrottles()
    now = fake_clock.now()

    # 分散式猜測：同帳號每個 IP 只錯 1 次（每個 (帳號, IP) 都遠低於 5 次門檻）
    for i in range(20):
        pair_key, account_key = login_keys("amy", f"10.0.0.{i}")
        t.login_account_ip.record_failure(pair_key, now)
        t.login_account.record_failure(account_key, now)
        t.login_account_ip.check(pair_key, now)

    with pytest.raises(RateLimitedError):
        t.login_account.check(login_keys("AMY", "10.9.9.9")[1], now)


def test_auth_throttle_rotating_username_hits_ip_limit(fake_clock: FakeClock) -> None:
    t = AuthThrottles()
    now = fake_clock.now()

    for i in range(20):
        t.login_ip.hit("1.2.3.4", now)
        t.login_account_ip.check(login_keys(f"user{i}", "1.2.3.4")[0], now)

    with pytest.raises(RateLimitedError):
        t.login_ip.hit("1.2.3.4", now)


def test_auth_throttle_case_variants_share_lockout(fake_clock: FakeClock) -> None:
    t = AuthThrottles()
    now = fake_clock.now()

    for username in ("amy", "Amy", "AMY", " amy", "amY "):
        t.login_account_ip.record_failure(login_keys(username, "1.2.3.4")[0], now)

    with pytest.raises(RateLimitedError):
        t.login_account_ip.check(login_keys("aMy", "1.2.3.4")[0], now)


def test_auth_throttle_dependency_override(fake_clock: FakeClock) -> None:
    app = FastAPI()
    register_exception_handlers(app)

    @app.post("/try")
    def _try(throttles: AuthThrottles = Depends(get_auth_throttles)) -> dict[str, str]:  # noqa: B008
        throttles.login_ip.hit("1.2.3.4", fake_clock.now())
        return {"ok": "1"}

    fresh = AuthThrottles()
    app.dependency_overrides[get_auth_throttles] = lambda: fresh
    client = TestClient(app)

    for _ in range(20):
        assert client.post("/try").status_code == 200
    blocked = client.post("/try")
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "too_many_attempts"
    assert blocked.headers["Retry-After"] == "300"

    # 端點操作的是 override 的新實例，不是單例
    assert get_auth_throttles() is not fresh
    assert len(fresh.login_ip) == 1
