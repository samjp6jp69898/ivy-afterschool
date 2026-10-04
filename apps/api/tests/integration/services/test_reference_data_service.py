"""BACKEND-115：app/services/reference_data_service.py（list_items）。"""

from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models.reference import ClosedDay, School, Subject
from app.schemas.reference import (
    ClosedDayCreateIn,
    ClosedDayOut,
    ClosedDayUpdateIn,
    NamedItemCreateIn,
    NamedItemOut,
    NamedItemUpdateIn,
    ReferenceListQuery,
    SchoolCreateIn,
    SchoolOut,
    SchoolUpdateIn,
)
from app.services.reference_data_service import create_item, list_items, update_item
from app.services.reference_specs import SPECS


def test_reference_list_subjects_order(db_session: Session) -> None:
    db_session.add(Subject(name="書法", sort_order=99, is_active=False))
    db_session.flush()

    everything = list_items(db_session, SPECS["subjects"], ReferenceListQuery(active_only=False))
    active = list_items(db_session, SPECS["subjects"], ReferenceListQuery(active_only=True))

    names = [item.name for item in everything]  # type: ignore[attr-defined]
    assert names[-1] == "書法"
    assert len(names) == 6
    assert "書法" not in [item.name for item in active]  # type: ignore[attr-defined]
    assert len(active) == 5
    orders = [item.sort_order for item in everything]  # type: ignore[attr-defined]
    assert orders == sorted(orders)


def test_reference_list_exam_types_active_only(db_session: Session) -> None:
    all_items = list_items(db_session, SPECS["exam-types"], ReferenceListQuery())
    assert all_items
    assert [i.sort_order for i in all_items] == sorted(i.sort_order for i in all_items)  # type: ignore[attr-defined]


def test_reference_list_closed_days_range(db_session: Session) -> None:
    for d in (date(2026, 10, 10), date(2026, 12, 25), date(2027, 1, 1)):
        db_session.add(ClosedDay(date=d, reason="測試"))
    db_session.flush()

    items = list_items(
        db_session,
        SPECS["closed-days"],
        ReferenceListQuery(date_from=date(2026, 10, 1), date_to=date(2026, 12, 31)),
    )

    assert all(isinstance(i, ClosedDayOut) for i in items)
    assert [i.date.isoformat() for i in items] == ["2026-12-25", "2026-10-10"]  # type: ignore[attr-defined]


def test_reference_list_closed_days_bounds_inclusive_and_ignores_active_only(
    db_session: Session,
) -> None:
    for d in (date(2026, 10, 1), date(2026, 12, 31), date(2026, 9, 30)):
        db_session.add(ClosedDay(date=d, reason=None))
    db_session.flush()

    items = list_items(
        db_session,
        SPECS["closed-days"],
        ReferenceListQuery(
            active_only=True, date_from=date(2026, 10, 1), date_to=date(2026, 12, 31)
        ),
    )

    assert [i.date.isoformat() for i in items] == ["2026-12-31", "2026-10-01"]  # type: ignore[attr-defined]


def test_reference_list_schools_by_name(db_session: Session) -> None:
    db_session.add(School(name="新生國小"))
    db_session.add(School(name="仁愛國小"))
    db_session.add(School(name="停用國小", is_active=False))
    db_session.flush()

    items = list_items(db_session, SPECS["schools"], ReferenceListQuery(active_only=True))

    names = [i.name for i in items]  # type: ignore[attr-defined]
    assert names.index("仁愛國小") < names.index("新生國小")
    assert "停用國小" not in names
    assert names == sorted(names)


def test_reference_create_subject(db_session: Session) -> None:
    out = create_item(db_session, SPECS["subjects"], NamedItemCreateIn(name="書法"))

    assert isinstance(out, NamedItemOut)
    assert out.name == "書法"
    assert out.is_active is True
    assert out.sort_order == 0
    stored = db_session.execute(select(Subject).where(Subject.id == out.id)).scalar_one()
    assert stored.name == "書法"


def test_reference_create_conflict(db_session: Session) -> None:
    with pytest.raises(AppError) as subject:
        create_item(db_session, SPECS["subjects"], NamedItemCreateIn(name="數學"))
    assert (subject.value.status, subject.value.code) == (409, "subject_name_taken")

    create_item(db_session, SPECS["schools"], SchoolCreateIn(name="ABC國小"))
    with pytest.raises(AppError) as school:
        create_item(db_session, SPECS["schools"], SchoolCreateIn(name=" abc國小 "))
    assert (school.value.status, school.value.code) == (409, "school_name_taken")

    # savepoint 已 rollback：session 仍可用，只有第一筆
    count = db_session.execute(
        select(func.count()).select_from(School).where(func.lower(School.name) == "abc國小")
    ).scalar_one()
    assert count == 1


def test_reference_create_exam_type_conflict(db_session: Session) -> None:
    create_item(db_session, SPECS["exam-types"], NamedItemCreateIn(name="隨堂小考X"))

    with pytest.raises(AppError) as exc:
        create_item(db_session, SPECS["exam-types"], NamedItemCreateIn(name="隨堂小考x"))

    assert exc.value.code == "exam_type_name_taken"


def test_reference_create_closed_day_conflict(db_session: Session) -> None:
    out = create_item(
        db_session,
        SPECS["closed-days"],
        ClosedDayCreateIn(date=date(2026, 10, 10), reason="國慶日"),
    )
    assert isinstance(out, ClosedDayOut)
    assert out.date == date(2026, 10, 10)
    assert out.reason == "國慶日"

    with pytest.raises(AppError) as exc:
        create_item(db_session, SPECS["closed-days"], ClosedDayCreateIn(date=date(2026, 10, 10)))

    assert (exc.value.status, exc.value.code) == (409, "closed_day_exists")


def test_reference_update_partial(db_session: Session) -> None:
    created = create_item(
        db_session, SPECS["subjects"], NamedItemCreateIn(name="書法", sort_order=5)
    )
    assert isinstance(created, NamedItemOut)

    out = update_item(db_session, SPECS["subjects"], created.id, NamedItemUpdateIn(is_active=False))

    assert isinstance(out, NamedItemOut)
    assert out.is_active is False
    assert out.sort_order == 5
    assert out.name == "書法"
    stored = db_session.execute(select(Subject).where(Subject.id == created.id)).scalar_one()
    assert (stored.name, stored.sort_order, stored.is_active) == ("書法", 5, False)


def test_reference_update_clear_nullable_field(db_session: Session) -> None:
    created = create_item(
        db_session, SPECS["schools"], SchoolCreateIn(name="仁愛國小", short_name="仁愛")
    )
    assert isinstance(created, SchoolOut)

    kept = update_item(db_session, SPECS["schools"], created.id, SchoolUpdateIn(is_active=False))
    cleared = update_item(
        db_session,
        SPECS["schools"],
        created.id,
        SchoolUpdateIn.model_validate({"short_name": None}),
    )

    assert kept.short_name == "仁愛"  # type: ignore[attr-defined]
    assert cleared.short_name is None  # type: ignore[attr-defined]
    assert cleared.is_active is False  # type: ignore[attr-defined]


def test_reference_update_closed_day_reason(db_session: Session) -> None:
    created = create_item(
        db_session, SPECS["closed-days"], ClosedDayCreateIn(date=date(2026, 10, 10), reason="國慶")
    )
    assert isinstance(created, ClosedDayOut)

    out = update_item(
        db_session, SPECS["closed-days"], created.id, ClosedDayUpdateIn(reason="國慶日")
    )

    assert isinstance(out, ClosedDayOut)
    assert (out.date, out.reason) == (date(2026, 10, 10), "國慶日")


def test_reference_update_not_found(db_session: Session) -> None:
    with pytest.raises(AppError) as exc:
        update_item(db_session, SPECS["exam-types"], uuid4(), NamedItemUpdateIn(name="新名稱"))

    assert (exc.value.status, exc.value.code) == (404, "exam_type_not_found")


def test_reference_update_conflict(db_session: Session) -> None:
    created = create_item(db_session, SPECS["subjects"], NamedItemCreateIn(name="書法"))
    assert isinstance(created, NamedItemOut)

    with pytest.raises(AppError) as exc:
        update_item(db_session, SPECS["subjects"], created.id, NamedItemUpdateIn(name="國語"))

    assert (exc.value.status, exc.value.code) == (409, "subject_name_taken")
    # savepoint 已 rollback：原名稱不變、session 仍可用
    stored = db_session.execute(select(Subject).where(Subject.id == created.id)).scalar_one()
    assert stored.name == "書法"
    # 改成自己的名稱（大小寫變化）不算衝突
    out = update_item(db_session, SPECS["subjects"], created.id, NamedItemUpdateIn(name="書法"))
    assert out.name == "書法"  # type: ignore[attr-defined]
