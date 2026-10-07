"""BACKEND-002：Clock Protocol、SystemClock、get_clock 與台北時區換算。
BACKEND-551：format_hm / format_taipei_hm（接送 / 作業 ETA 的 HH:MM 輸出），取代 pickup/views、
pickup/requests、parent_today_service 三份逐字相同的私有 _hm / _taipei_hm。"""

import ast
from datetime import UTC, date, datetime, time, timedelta, timezone
from importlib.resources import files

import pytest

from app.core.clock import (
    TAIPEI,
    Clock,
    SystemClock,
    combine_taipei,
    format_hm,
    format_taipei_hm,
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


# --- BACKEND-551：format_hm / format_taipei_hm ---------------------------------------------------


def test_format_hm_formats_time_and_none() -> None:
    assert format_hm(time(9, 5)) == "09:05"
    assert format_hm(time(0, 0)) == "00:00"
    assert format_hm(time(23, 59)) == "23:59"
    assert format_hm(time(17, 30, 45)) == "17:30"  # 不補秒
    assert format_hm(None) is None


def test_format_taipei_hm_converts_utc_and_midnight() -> None:
    assert format_taipei_hm(datetime(2026, 10, 7, 1, 30, tzinfo=UTC)) == "09:30"
    assert format_taipei_hm(datetime(2026, 10, 6, 16, 0, tzinfo=UTC)) == "00:00"  # 跨午夜
    # 其他時區的 aware datetime 一樣先轉台北時間
    tokyo = timezone(timedelta(hours=9))
    assert format_taipei_hm(datetime(2026, 10, 7, 10, 0, tzinfo=tokyo)) == "09:00"
    assert format_taipei_hm(None) is None
    # 與 to_taipei 一致：naive 一律拒絕
    with pytest.raises(ValueError, match="naive"):
        format_taipei_hm(datetime(2026, 10, 7, 9, 0))  # noqa: DTZ001  刻意構造 naive


_PRIVATE_COPIES = frozenset({"_hm", "_taipei_hm"})
_PUBLIC_FORMATTERS = frozenset({"format_hm", "format_taipei_hm"})
_HM_CONSUMERS = (
    "services/pickup/views.py",
    "services/pickup/requests.py",
    "services/parent_today_service.py",
)


def _module_level_functions(tree: ast.Module) -> set[str]:
    return {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}


def _names_imported_from_clock(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "app.core.clock":
            names.update(alias.name for alias in node.names)
    return names


@pytest.mark.parametrize("relative_path", _HM_CONSUMERS)
def test_no_private_hm_copies_remain(relative_path: str) -> None:
    """三個模組不再於模組層級定義 _hm / _taipei_hm，且都從 app.core.clock 匯入兩個公開函式。"""
    source = (files("app") / relative_path).read_text(encoding="utf-8")
    tree = ast.parse(source, filename=relative_path)

    leftovers = sorted(_module_level_functions(tree) & _PRIVATE_COPIES)
    missing = sorted(_PUBLIC_FORMATTERS - _names_imported_from_clock(tree))

    assert leftovers == [], f"{relative_path} 仍有私有副本：{leftovers}"
    assert missing == [], f"{relative_path} 未從 app.core.clock 匯入：{missing}"
