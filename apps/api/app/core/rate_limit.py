"""BACKEND-012：in-process 滑動視窗節流與連續失敗鎖定。

api 固定單一實例單 worker（architecture_decisions §2），狀態放記憶體即可；介面不綁實作，
日後換 DB / Redis 只需提供同名方法。移植 ivy ``utils/rate_limit.py::SlidingWindowLimiter``，
去掉 tenant key 與 Postgres 實作。

- 不持有 clock：``now`` 由呼叫端傳入（dependency override 的 FakeClock 才能生效），必須 aware。
- 執行緒安全：sync endpoint 在 threadpool 並行執行，所有狀態操作都在 ``threading.Lock`` 內。
- 過期 key：每次操作清理當前 key；全表清掃至多每個視窗（鎖定期）一次，避免每次操作 O(n)。
- 實例由使用端以 FastAPI dependency 提供（例如 BACKEND-040 ``get_auth_throttles``），
  測試以 dependency override 換新實例，不需要全域 reset。
"""

from __future__ import annotations

import math
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.core.errors import RateLimitedError

_TOO_MANY_REQUESTS = "請求過於頻繁，請稍後再試"
_TOO_MANY_FAILURES = "嘗試次數過多，請稍後再試"


def _require_positive(**values: int) -> None:
    for name, value in values.items():
        if value < 1:
            raise ValueError(f"{name} 必須 >= 1，收到 {value}")


def _require_aware(now: datetime) -> None:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now 必須是 aware datetime")


def _ceil_seconds(delta: timedelta) -> int:
    """剩餘秒數無條件進位，至少 1（呼叫端只在剩餘 > 0 時呼叫）。"""
    return max(1, math.ceil(delta.total_seconds()))


class SlidingWindowLimiter:
    """同一 key 在任意 ``window_seconds`` 內最多 ``max_hits`` 次；被拒的 hit 不記錄。"""

    def __init__(self, *, max_hits: int, window_seconds: int) -> None:
        _require_positive(max_hits=max_hits, window_seconds=window_seconds)
        self._max_hits = max_hits
        self._window = timedelta(seconds=window_seconds)
        self._hits: dict[str, deque[datetime]] = {}
        self._lock = threading.Lock()
        self._last_sweep: datetime | None = None

    def __len__(self) -> int:
        with self._lock:
            return len(self._hits)

    def hit(self, key: str, now: datetime) -> None:
        _require_aware(now)
        with self._lock:
            self._maybe_sweep(now)
            hits = self._hits.get(key)
            if hits is None:
                hits = self._hits[key] = deque()
            self._drop_expired(hits, now)
            if len(hits) >= self._max_hits:
                raise RateLimitedError(
                    _TOO_MANY_REQUESTS,
                    retry_after_seconds=_ceil_seconds(hits[0] + self._window - now),
                )
            hits.append(now)

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)

    def _drop_expired(self, hits: deque[datetime], now: datetime) -> None:
        # 滿一個視窗即到期：now - t >= window
        while hits and now - hits[0] >= self._window:
            hits.popleft()

    def _maybe_sweep(self, now: datetime) -> None:
        if self._last_sweep is not None and now - self._last_sweep < self._window:
            return
        self._last_sweep = now
        for key in list(self._hits):
            hits = self._hits[key]
            self._drop_expired(hits, now)
            if not hits:
                del self._hits[key]


@dataclass
class _FailureState:
    failures: int = 0
    last_failure_at: datetime | None = None
    locked_until: datetime | None = None


class FailureLockout:
    """連續失敗達 ``threshold`` 次 → 鎖定 ``lockout_seconds``，計數歸零。

    - 失敗計數自最後一次失敗起 ``lockout_seconds`` 內沒有新失敗即過期。
    - 鎖定中的 ``record_failure``（已通過 check 的並行請求晚到）不延長鎖定、不累計。
    """

    def __init__(self, *, threshold: int, lockout_seconds: int) -> None:
        _require_positive(threshold=threshold, lockout_seconds=lockout_seconds)
        self._threshold = threshold
        self._lockout = timedelta(seconds=lockout_seconds)
        self._states: dict[str, _FailureState] = {}
        self._lock = threading.Lock()
        self._last_sweep: datetime | None = None

    def __len__(self) -> int:
        with self._lock:
            return len(self._states)

    def check(self, key: str, now: datetime) -> None:
        _require_aware(now)
        with self._lock:
            self._maybe_sweep(now)
            state = self._current(key, now)
            if state is not None and state.locked_until is not None:
                raise RateLimitedError(
                    _TOO_MANY_FAILURES,
                    retry_after_seconds=_ceil_seconds(state.locked_until - now),
                )

    def record_failure(self, key: str, now: datetime) -> None:
        _require_aware(now)
        with self._lock:
            self._maybe_sweep(now)
            state = self._current(key, now)
            if state is None:
                state = self._states[key] = _FailureState()
            elif state.locked_until is not None:
                return
            state.failures += 1
            state.last_failure_at = now
            if state.failures >= self._threshold:
                state.failures = 0
                state.locked_until = now + self._lockout

    def clear(self, key: str) -> None:
        with self._lock:
            self._states.pop(key, None)

    def _expired(self, state: _FailureState, now: datetime) -> bool:
        if state.locked_until is not None:
            return now >= state.locked_until
        return state.last_failure_at is None or now - state.last_failure_at >= self._lockout

    def _current(self, key: str, now: datetime) -> _FailureState | None:
        """回傳仍有效的狀態；過期的順手移除。"""
        state = self._states.get(key)
        if state is not None and self._expired(state, now):
            del self._states[key]
            return None
        return state

    def _maybe_sweep(self, now: datetime) -> None:
        if self._last_sweep is not None and now - self._last_sweep < self._lockout:
            return
        self._last_sweep = now
        for key in [k for k, s in self._states.items() if self._expired(s, now)]:
            del self._states[key]
