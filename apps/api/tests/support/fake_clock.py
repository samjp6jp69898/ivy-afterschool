"""FakeClock 測試替身（INFRA-011）。

與 `app/core/clock.py` 的 `Clock` Protocol 結構相容：`now()` 回傳 UTC aware datetime，
`today()` 回傳 Asia/Taipei 的日期。時間只透過注入取得，不 monkeypatch datetime 模組。
"""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

TAIPEI = ZoneInfo("Asia/Taipei")


def _require_aware(when: datetime) -> datetime:
    if when.tzinfo is None or when.tzinfo.utcoffset(when) is None:
        raise ValueError("FakeClock 需要 aware datetime")
    return when.astimezone(UTC)


class FakeClock:
    def __init__(self, start: datetime) -> None:
        self._now = _require_aware(start)

    def now(self) -> datetime:
        return self._now

    def today(self) -> date:
        return self._now.astimezone(TAIPEI).date()

    def set(self, when: datetime) -> None:
        self._now = _require_aware(when)

    def advance(self, delta: timedelta | None = None, **kwargs: float) -> None:
        self._now += (delta or timedelta()) + timedelta(**kwargs)
