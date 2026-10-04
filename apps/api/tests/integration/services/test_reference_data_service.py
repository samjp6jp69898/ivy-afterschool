"""BACKEND-115：app/services/reference_data_service.py（list_items）。"""

from datetime import date

from sqlalchemy.orm import Session

from app.models.reference import ClosedDay, School, Subject
from app.schemas.reference import ClosedDayOut, ReferenceListQuery
from app.services.reference_data_service import list_items
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
