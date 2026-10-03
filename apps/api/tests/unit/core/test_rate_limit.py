"""BACKEND-012：in-process SlidingWindowLimiter 與 FailureLockout。"""

import threading
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.core.errors import RateLimitedError
from app.core.rate_limit import FailureLockout, SlidingWindowLimiter
from tests.support.fake_clock import FakeClock


def _retry_after(exc_info: pytest.ExceptionInfo[RateLimitedError]) -> int:
    exc = exc_info.value
    assert exc.status == 429
    assert exc.details == {"retry_after_seconds": exc.retry_after_seconds}
    return exc.retry_after_seconds


# --- SlidingWindowLimiter ---------------------------------------------------------------


def test_rate_limit_window_exceeded(fake_clock: FakeClock) -> None:
    limiter = SlidingWindowLimiter(max_hits=3, window_seconds=60)
    for _ in range(3):
        limiter.hit("1.2.3.4", fake_clock.now())

    with pytest.raises(RateLimitedError) as exc_info:
        limiter.hit("1.2.3.4", fake_clock.now())

    assert _retry_after(exc_info) == 60


def test_rate_limit_window_slides(fake_clock: FakeClock) -> None:
    limiter = SlidingWindowLimiter(max_hits=3, window_seconds=60)
    for _ in range(3):
        limiter.hit("1.2.3.4", fake_clock.now())

    fake_clock.advance(seconds=61)

    limiter.hit("1.2.3.4", fake_clock.now())


def test_rate_limit_window_boundary_exact(fake_clock: FakeClock) -> None:
    limiter = SlidingWindowLimiter(max_hits=3, window_seconds=60)
    for _ in range(3):
        limiter.hit("k", fake_clock.now())

    fake_clock.advance(seconds=59)
    with pytest.raises(RateLimitedError) as exc_info:
        limiter.hit("k", fake_clock.now())
    assert _retry_after(exc_info) == 1

    # 剛好滿一個視窗：最早那筆到期
    fake_clock.advance(seconds=1)
    limiter.hit("k", fake_clock.now())


def test_rate_limit_retry_after_uses_oldest_hit_and_rounds_up(fake_clock: FakeClock) -> None:
    limiter = SlidingWindowLimiter(max_hits=3, window_seconds=60)
    limiter.hit("k", fake_clock.now())
    fake_clock.advance(seconds=10)
    limiter.hit("k", fake_clock.now())
    fake_clock.advance(seconds=10)
    limiter.hit("k", fake_clock.now())
    fake_clock.advance(seconds=10.5)

    with pytest.raises(RateLimitedError) as exc_info:
        limiter.hit("k", fake_clock.now())
    # 最早一筆在 t0，到期 t0+60；現在 t0+30.5 → 剩 29.5 秒，無條件進位
    assert _retry_after(exc_info) == 30

    # 第一筆到期後只釋出一個名額：第二筆（t0+10）還在視窗內
    fake_clock.advance(seconds=29.5)
    limiter.hit("k", fake_clock.now())
    with pytest.raises(RateLimitedError) as exc_info:
        limiter.hit("k", fake_clock.now())
    assert _retry_after(exc_info) == 10


def test_rate_limit_rejected_hit_is_not_recorded(fake_clock: FakeClock) -> None:
    limiter = SlidingWindowLimiter(max_hits=2, window_seconds=60)
    limiter.hit("k", fake_clock.now())
    limiter.hit("k", fake_clock.now())
    fake_clock.advance(seconds=30)
    for _ in range(5):
        with pytest.raises(RateLimitedError):
            limiter.hit("k", fake_clock.now())

    # 被拒的嘗試不延長封鎖：最早兩筆到期就恢復
    fake_clock.advance(seconds=30)
    limiter.hit("k", fake_clock.now())
    limiter.hit("k", fake_clock.now())


def test_rate_limit_keys_isolated(fake_clock: FakeClock) -> None:
    limiter = SlidingWindowLimiter(max_hits=3, window_seconds=60)
    for _ in range(3):
        limiter.hit("A", fake_clock.now())

    limiter.hit("B", fake_clock.now())

    with pytest.raises(RateLimitedError):
        limiter.hit("A", fake_clock.now())


def test_rate_limit_reset(fake_clock: FakeClock) -> None:
    limiter = SlidingWindowLimiter(max_hits=1, window_seconds=60)
    limiter.hit("k", fake_clock.now())
    limiter.hit("other", fake_clock.now())

    limiter.reset("k")
    limiter.reset("never-seen")

    limiter.hit("k", fake_clock.now())
    with pytest.raises(RateLimitedError):
        limiter.hit("other", fake_clock.now())


def test_rate_limit_expired_keys_are_swept(fake_clock: FakeClock) -> None:
    limiter = SlidingWindowLimiter(max_hits=5, window_seconds=60)
    for i in range(100):
        limiter.hit(f"ip-{i}", fake_clock.now())
    assert len(limiter) == 100

    fake_clock.advance(seconds=60)
    limiter.hit("fresh", fake_clock.now())

    assert len(limiter) == 1


def test_rate_limit_thread_safe(fake_clock: FakeClock) -> None:
    limiter = SlidingWindowLimiter(max_hits=100, window_seconds=60)
    now = fake_clock.now()
    errors: list[BaseException] = []
    barrier = threading.Barrier(10)

    def worker() -> None:
        barrier.wait()
        for _ in range(10):
            try:
                limiter.hit("k", now)
            except BaseException as exc:  # noqa: BLE001  收集後在主執行緒斷言
                errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    with pytest.raises(RateLimitedError):
        limiter.hit("k", now)


def test_rate_limit_thread_safe_exact_admission(fake_clock: FakeClock) -> None:
    limiter = SlidingWindowLimiter(max_hits=100, window_seconds=60)
    now = fake_clock.now()
    admitted: list[int] = []
    rejected: list[int] = []
    lock = threading.Lock()
    barrier = threading.Barrier(20)

    def worker() -> None:
        barrier.wait()
        for _ in range(10):
            try:
                limiter.hit("k", now)
            except RateLimitedError:
                with lock:
                    rejected.append(1)
            else:
                with lock:
                    admitted.append(1)

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(admitted) == 100
    assert len(rejected) == 100


@pytest.mark.parametrize(("max_hits", "window_seconds"), [(0, 60), (3, 0), (-1, 60)])
def test_rate_limit_rejects_invalid_config(max_hits: int, window_seconds: int) -> None:
    with pytest.raises(ValueError, match="必須 >= 1"):
        SlidingWindowLimiter(max_hits=max_hits, window_seconds=window_seconds)


def test_rate_limit_rejects_naive_now() -> None:
    limiter = SlidingWindowLimiter(max_hits=3, window_seconds=60)

    with pytest.raises(ValueError, match="aware"):
        limiter.hit("k", datetime(2026, 9, 1, 9, 0))  # noqa: DTZ001  刻意構造 naive


# --- FailureLockout ---------------------------------------------------------------------


def test_failure_lockout_threshold(fake_clock: FakeClock) -> None:
    lockout = FailureLockout(threshold=5, lockout_seconds=900)
    key = "amy|1.2.3.4"
    for _ in range(4):
        lockout.record_failure(key, fake_clock.now())
    lockout.check(key, fake_clock.now())

    lockout.record_failure(key, fake_clock.now())
    with pytest.raises(RateLimitedError) as exc_info:
        lockout.check(key, fake_clock.now())
    assert _retry_after(exc_info) == 900

    fake_clock.advance(seconds=899)
    with pytest.raises(RateLimitedError) as exc_info:
        lockout.check(key, fake_clock.now())
    assert _retry_after(exc_info) == 1

    fake_clock.advance(seconds=2)
    lockout.check(key, fake_clock.now())


def test_failure_lockout_expires_exactly_at_deadline(fake_clock: FakeClock) -> None:
    lockout = FailureLockout(threshold=1, lockout_seconds=900)
    lockout.record_failure("k", fake_clock.now())

    fake_clock.advance(seconds=899.5)
    with pytest.raises(RateLimitedError) as exc_info:
        lockout.check("k", fake_clock.now())
    assert _retry_after(exc_info) == 1

    fake_clock.advance(seconds=0.5)
    lockout.check("k", fake_clock.now())


def test_failure_lockout_counter_reset_after_lock(fake_clock: FakeClock) -> None:
    lockout = FailureLockout(threshold=5, lockout_seconds=900)
    for _ in range(5):
        lockout.record_failure("k", fake_clock.now())
    fake_clock.advance(seconds=900)
    lockout.check("k", fake_clock.now())

    # 鎖定時計數已歸零：期滿後要再連錯滿 threshold 次才會再鎖
    for _ in range(4):
        lockout.record_failure("k", fake_clock.now())
    lockout.check("k", fake_clock.now())
    lockout.record_failure("k", fake_clock.now())
    with pytest.raises(RateLimitedError):
        lockout.check("k", fake_clock.now())


def test_failure_lockout_failures_during_lock_do_not_extend(fake_clock: FakeClock) -> None:
    lockout = FailureLockout(threshold=2, lockout_seconds=900)
    lockout.record_failure("k", fake_clock.now())
    lockout.record_failure("k", fake_clock.now())

    fake_clock.advance(seconds=600)
    # 已通過 check 的並行請求在鎖定後才回報失敗：不延長、不累計
    lockout.record_failure("k", fake_clock.now())
    lockout.record_failure("k", fake_clock.now())
    with pytest.raises(RateLimitedError) as exc_info:
        lockout.check("k", fake_clock.now())
    assert _retry_after(exc_info) == 300

    fake_clock.advance(seconds=300)
    lockout.check("k", fake_clock.now())
    lockout.record_failure("k", fake_clock.now())
    lockout.check("k", fake_clock.now())


def test_failure_lockout_clear(fake_clock: FakeClock) -> None:
    lockout = FailureLockout(threshold=5, lockout_seconds=900)
    for _ in range(4):
        lockout.record_failure("k", fake_clock.now())

    lockout.clear("k")

    for _ in range(4):
        lockout.record_failure("k", fake_clock.now())
    lockout.check("k", fake_clock.now())


def test_failure_lockout_clear_lifts_active_lock(fake_clock: FakeClock) -> None:
    lockout = FailureLockout(threshold=1, lockout_seconds=900)
    lockout.record_failure("k", fake_clock.now())

    lockout.clear("k")
    lockout.clear("never-seen")

    lockout.check("k", fake_clock.now())


def test_failure_lockout_keys_isolated(fake_clock: FakeClock) -> None:
    lockout = FailureLockout(threshold=2, lockout_seconds=900)
    lockout.record_failure("amy|1.2.3.4", fake_clock.now())
    lockout.record_failure("amy|1.2.3.4", fake_clock.now())
    lockout.record_failure("bob|1.2.3.4", fake_clock.now())

    lockout.check("bob|1.2.3.4", fake_clock.now())
    lockout.check("amy|5.6.7.8", fake_clock.now())
    with pytest.raises(RateLimitedError):
        lockout.check("amy|1.2.3.4", fake_clock.now())


def test_failure_lockout_idle_failures_expire(fake_clock: FakeClock) -> None:
    lockout = FailureLockout(threshold=5, lockout_seconds=900)
    for _ in range(4):
        lockout.record_failure("k", fake_clock.now())

    # 最後一次失敗後 lockout_seconds 內沒有新失敗 → 計數過期
    fake_clock.advance(seconds=900)
    lockout.record_failure("k", fake_clock.now())
    lockout.check("k", fake_clock.now())

    # 尚未滿 lockout_seconds 時計數保留
    for _ in range(3):
        fake_clock.advance(seconds=899)
        lockout.record_failure("k", fake_clock.now())
    lockout.record_failure("k", fake_clock.now())
    with pytest.raises(RateLimitedError):
        lockout.check("k", fake_clock.now())


def test_failure_lockout_expired_keys_are_swept(fake_clock: FakeClock) -> None:
    lockout = FailureLockout(threshold=3, lockout_seconds=900)
    for i in range(50):
        lockout.record_failure(f"user{i}|1.2.3.4", fake_clock.now())
    for _ in range(3):
        lockout.record_failure("locked", fake_clock.now())
    assert len(lockout) == 51

    fake_clock.advance(seconds=900)
    lockout.check("fresh", fake_clock.now())

    assert len(lockout) == 0


def test_failure_lockout_thread_safe(fake_clock: FakeClock) -> None:
    lockout = FailureLockout(threshold=1000, lockout_seconds=900)
    now = fake_clock.now()
    barrier = threading.Barrier(10)

    def worker() -> None:
        barrier.wait()
        for _ in range(99):
            lockout.record_failure("k", now)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # 990 次精確累計：再 9 次不鎖，第 10 次才鎖
    for _ in range(9):
        lockout.record_failure("k", now)
    lockout.check("k", now)
    lockout.record_failure("k", now)
    with pytest.raises(RateLimitedError):
        lockout.check("k", now)


@pytest.mark.parametrize(("threshold", "lockout_seconds"), [(0, 900), (5, 0)])
def test_failure_lockout_rejects_invalid_config(threshold: int, lockout_seconds: int) -> None:
    with pytest.raises(ValueError, match="必須 >= 1"):
        FailureLockout(threshold=threshold, lockout_seconds=lockout_seconds)


def test_failure_lockout_accepts_non_utc_aware_now() -> None:
    lockout = FailureLockout(threshold=1, lockout_seconds=60)
    start = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)
    lockout.record_failure("k", start)

    taipei_later = (start + timedelta(seconds=30)).astimezone(ZoneInfo("Asia/Taipei"))
    with pytest.raises(RateLimitedError) as exc_info:
        lockout.check("k", taipei_later)
    assert _retry_after(exc_info) == 30
