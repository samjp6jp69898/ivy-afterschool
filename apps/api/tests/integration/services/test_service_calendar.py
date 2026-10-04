"""BACKEND-123：app/services/service_calendar.py（is_service_day、service_hours_for、list_service_days）。

營業時段讀 seed 預設（週一到週五 12:00~19:00、週六不營業、週日不列），休息日在 db_session 內新增、
測試結束 rollback。
"""

import json
from collections.abc import Iterator
from datetime import date
from typing import Any

import pytest
from sqlalchemy import event, text
from sqlalchemy.orm import Session

from app.core.settings_registry import DayHours
from app.models.reference import ClosedDay
from app.services.service_calendar import (
    MAX_RANGE_DAYS,
    is_service_day,
    list_service_days,
    service_hours_for,
)
from app.services.settings_service import clear_settings_cache


@pytest.fixture(autouse=True)
def _clear_cache() -> Iterator[None]:
    clear_settings_cache()
    yield
    clear_settings_cache()


def _closed(session: Session, d: date, reason: str = "休息日") -> None:
    session.add(ClosedDay(date=d, reason=reason))
    session.flush()


def _set_sat_open(session: Session) -> None:
    session.execute(
        text(
            "update public.system_settings "
            "set value = jsonb_set(value, '{sat}', cast(:sat as jsonb)) "
            "where key = 'org.service_hours'"
        ),
        {"sat": json.dumps({"open": True, "start": "09:00", "end": "12:00"})},
    )
    clear_settings_cache()


def test_service_calendar_weekday_and_sunday(db_session: Session) -> None:
    assert date(2026, 9, 1).weekday() == 1  # 週二
    assert is_service_day(db_session, date(2026, 9, 1)) is True
    assert is_service_day(db_session, date(2026, 9, 4)) is True  # 週五
    assert is_service_day(db_session, date(2026, 9, 6)) is False  # 週日
    assert is_service_day(db_session, date(2026, 9, 5)) is False  # 週六 sat.open=false

    # 週六依設定：改成營業後即為營業日
    _set_sat_open(db_session)
    assert is_service_day(db_session, date(2026, 9, 5)) is True
    assert is_service_day(db_session, date(2026, 9, 6)) is False


def test_service_calendar_closed_day(db_session: Session) -> None:
    assert is_service_day(db_session, date(2026, 9, 1)) is True
    _closed(db_session, date(2026, 9, 1))

    assert is_service_day(db_session, date(2026, 9, 1)) is False
    # 休息日不影響營業時段（時段只看星期），也不影響其他日期
    assert service_hours_for(db_session, date(2026, 9, 1)) == DayHours(
        open=True, start="12:00", end="19:00"
    )
    assert is_service_day(db_session, date(2026, 9, 2)) is True


def test_service_calendar_hours(db_session: Session) -> None:
    assert service_hours_for(db_session, date(2026, 9, 1)) == DayHours(
        open=True, start="12:00", end="19:00"
    )
    assert service_hours_for(db_session, date(2026, 9, 6)) is None  # 週日
    assert service_hours_for(db_session, date(2026, 9, 5)) is None  # 週六 open=false

    _set_sat_open(db_session)
    assert service_hours_for(db_session, date(2026, 9, 5)) == DayHours(
        open=True, start="09:00", end="12:00"
    )


def test_service_calendar_list(db_session: Session) -> None:
    _closed(db_session, date(2026, 9, 3))
    # 區間外的休息日不影響
    _closed(db_session, date(2026, 9, 8))
    closed_queries: list[str] = []

    def _count(
        conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, executemany: bool
    ) -> None:
        if "closed_days" in statement:
            closed_queries.append(statement)

    bind = db_session.connection()
    event.listen(bind, "before_cursor_execute", _count)
    try:
        days = list_service_days(db_session, date(2026, 9, 1), date(2026, 9, 7))
    finally:
        event.remove(bind, "before_cursor_execute", _count)

    # 9/3 休息日、9/5 週六不營業、9/6 週日、9/7 週一營業
    assert days == [date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 4), date(2026, 9, 7)]
    assert len(closed_queries) == 1

    # 含頭尾、單日區間、全休區間
    assert list_service_days(db_session, date(2026, 9, 4), date(2026, 9, 4)) == [date(2026, 9, 4)]
    assert list_service_days(db_session, date(2026, 9, 3), date(2026, 9, 3)) == []
    assert list_service_days(db_session, date(2026, 9, 5), date(2026, 9, 6)) == []
    assert list_service_days(db_session, date(2026, 9, 7), date(2026, 9, 11)) == [
        date(2026, 9, 7),
        date(2026, 9, 9),
        date(2026, 9, 10),
        date(2026, 9, 11),
    ]


def test_service_calendar_range_errors(db_session: Session) -> None:
    assert MAX_RANGE_DAYS == 366
    with pytest.raises(ValueError, match="end"):
        list_service_days(db_session, date(2026, 9, 2), date(2026, 9, 1))
    with pytest.raises(ValueError, match="366"):
        list_service_days(db_session, date(2026, 1, 1), date(2027, 2, 5))  # 跨 400 天
    with pytest.raises(ValueError, match="366"):
        list_service_days(db_session, date(2026, 1, 1), date(2027, 1, 2))  # 367 天
    # 剛好 366 天可以
    days = list_service_days(db_session, date(2026, 1, 1), date(2027, 1, 1))
    assert days[0] == date(2026, 1, 1)  # 週四
    assert days[-1] == date(2027, 1, 1)  # 週五
