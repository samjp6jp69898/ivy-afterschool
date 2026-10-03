"""BACKEND-002：Clock Protocol、SystemClock、get_clock 與台北時區換算。"""

from datetime import UTC, date, datetime, time, timedelta, timezone

import pytest

from app.core.clock import (
    TAIPEI,
    Clock,
    SystemClock,
    combine_taipei,
    get_clock,
    taipei_day_bounds,
    to_taipei,
)
from tests.support.fake_clock import FakeClock


def _today_via_protocol(clock: Clock) -> date:
    """只依賴 Clock 介面；mypy 檢查 FakeClock / SystemClock 都能傳入。"""
    return clock.today()


def test_clock_system_now_is_utc_aware() -> None:
    now = SystemClock().now()

    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(0)
    assert now.tzinfo == UTC


def test_clock_system_today_matches_taipei_date() -> None:
    clock = SystemClock()

    before = clock.now().astimezone(TAIPEI).date()
    today = clock.today()
    after = clock.now().astimezone(TAIPEI).date()

    # 測試期間可能剛好跨過台北午夜，today 必須落在前後兩次取樣之間
    assert today in {before, after}


def test_clock_today_uses_taipei_via_fake() -> None:
    at_midnight = FakeClock(datetime(2026, 9, 1, 16, 0, tzinfo=UTC))
    just_before = FakeClock(datetime(2026, 9, 1, 15, 59, 59, tzinfo=UTC))

    assert _today_via_protocol(at_midnight) == date(2026, 9, 2)
    assert _today_via_protocol(just_before) == date(2026, 9, 1)
    assert isinstance(at_midnight, Clock)
    assert isinstance(SystemClock(), Clock)


def test_clock_to_taipei_rejects_naive() -> None:
    with pytest.raises(ValueError, match="naive"):
        to_taipei(datetime(2026, 9, 1, 9, 0))  # noqa: DTZ001  刻意構造 naive

    converted = to_taipei(datetime(2026, 9, 1, 1, 0, tzinfo=UTC))
    assert converted.hour == 9
    assert converted.utcoffset() == timedelta(hours=8)
    # 其他時區的 aware datetime 一樣換算
    tokyo = timezone(timedelta(hours=9))
    assert to_taipei(datetime(2026, 9, 1, 10, 0, tzinfo=tokyo)).hour == 9


def test_clock_taipei_day_bounds() -> None:
    start, end = taipei_day_bounds(date(2026, 9, 1))

    assert start == datetime(2026, 8, 31, 16, 0, tzinfo=UTC)
    assert end == datetime(2026, 9, 1, 16, 0, tzinfo=UTC)
    assert start.tzinfo == UTC
    assert end.tzinfo == UTC
    # 跨月、跨年
    assert taipei_day_bounds(date(2027, 1, 1)) == (
        datetime(2026, 12, 31, 16, 0, tzinfo=UTC),
        datetime(2027, 1, 1, 16, 0, tzinfo=UTC),
    )


def test_clock_combine_taipei() -> None:
    combined = combine_taipei(date(2026, 9, 1), time(17, 30))

    assert combined == datetime(2026, 9, 1, 9, 30, tzinfo=UTC)
    assert combined.tzinfo == UTC
    # 台北清晨 → UTC 前一天
    assert combine_taipei(date(2026, 9, 1), time(7, 0)) == datetime(2026, 8, 31, 23, 0, tzinfo=UTC)


def test_clock_get_clock_returns_singleton() -> None:
    assert get_clock() is get_clock()
    assert isinstance(get_clock(), SystemClock)
