"""BACKEND-002：全專案取得「現在」與「今天」的唯一入口（architecture_decisions §5）。

- ``now()`` 一律回傳 UTC aware datetime；``today()`` 以 Asia/Taipei 判斷。
- ruff banned-api 禁止其他檔案呼叫 ``datetime.now`` / ``date.today``，本檔以 per-file-ignores 豁免。
- service 方法一律以參數接收 ``clock: Clock``，不得自行建立 SystemClock；app 層測試以
  ``app.dependency_overrides[get_clock] = lambda: fake_clock`` 注入（FakeClock 見 tests/support）。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Final, Protocol, runtime_checkable
from zoneinfo import ZoneInfo

TAIPEI: Final = ZoneInfo("Asia/Taipei")


@runtime_checkable
class Clock(Protocol):
    def now(self) -> datetime:
        """UTC aware datetime。"""
        ...

    def today(self) -> date:
        """now() 轉 Asia/Taipei 的日期。"""
        ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)

    def today(self) -> date:
        return self.now().astimezone(TAIPEI).date()


_SYSTEM_CLOCK: Final = SystemClock()


def get_clock() -> Clock:
    """FastAPI dependency：回傳 module-level SystemClock 單例。"""
    return _SYSTEM_CLOCK


def to_taipei(dt: datetime) -> datetime:
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("to_taipei 不接受 naive datetime")
    return dt.astimezone(TAIPEI)


def combine_taipei(d: date, t: time) -> datetime:
    """台北當地日期 + 時間 → UTC aware（接送 HH:MM 轉時間點用）。"""
    return datetime.combine(d, t, tzinfo=TAIPEI).astimezone(UTC)


def taipei_day_bounds(d: date) -> tuple[datetime, datetime]:
    """該台北日 00:00 與次日 00:00（UTC aware），查詢區間為 [start, end)。"""
    return combine_taipei(d, time(0, 0)), combine_taipei(d + timedelta(days=1), time(0, 0))
