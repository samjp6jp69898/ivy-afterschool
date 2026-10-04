"""BACKEND-123：營業日判斷（domain_spec M4：營業日 = 非 closed_days 且在 org.service_hours 內）。

營運模組判斷營業日的唯一入口：每日出勤初始化、月出勤報表、請假套用出勤都用它。
營業時段以 BACKEND-108 ``get_setting`` 讀取（走 cache）；週日一律不營業（ServiceHours 不列週日）。
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.settings_registry import ORG_SERVICE_HOURS, DayHours, ServiceHours
from app.models.reference import ClosedDay
from app.services.settings_service import get_setting

# list_service_days 區間上限（含頭尾），涵蓋閏年整年
MAX_RANGE_DAYS: Final = 366

# date.weekday()：0=週一 … 5=週六；週日（6）不在 ServiceHours 內
_WEEKDAY_FIELDS: Final = ("mon", "tue", "wed", "thu", "fri", "sat")


def _hours_of(hours: ServiceHours, d: date) -> DayHours | None:
    weekday = d.weekday()
    if weekday >= len(_WEEKDAY_FIELDS):
        return None
    day: DayHours = getattr(hours, _WEEKDAY_FIELDS[weekday])
    return day if day.open else None


def service_hours_for(session: Session, d: date) -> DayHours | None:
    """週日 → None；該星期 open=false → None；否則回該日的 DayHours。"""
    return _hours_of(get_setting(session, ORG_SERVICE_HOURS), d)


def is_service_day(session: Session, d: date) -> bool:
    """service_hours_for 非 None 且 d 不在 closed_days。"""
    if service_hours_for(session, d) is None:
        return False
    closed = session.execute(select(ClosedDay.id).where(ClosedDay.date == d)).first()
    return closed is None


def list_service_days(session: Session, start: date, end: date) -> list[date]:
    """[start, end] 內的營業日，遞增排序；end < start 或超過 MAX_RANGE_DAYS 天 → ValueError。

    closed_days 一次查出，不逐日查。
    """
    if end < start:
        raise ValueError(f"end 不可早於 start：{start} > {end}")
    span = (end - start).days + 1
    if span > MAX_RANGE_DAYS:
        raise ValueError(f"區間最多 {MAX_RANGE_DAYS} 天：{start} ~ {end} 共 {span} 天")

    hours = get_setting(session, ORG_SERVICE_HOURS)
    closed = set(
        session.execute(
            select(ClosedDay.date).where(ClosedDay.date >= start, ClosedDay.date <= end)
        ).scalars()
    )
    return [
        d
        for d in (start + timedelta(days=offset) for offset in range(span))
        if d not in closed and _hours_of(hours, d) is not None
    ]
