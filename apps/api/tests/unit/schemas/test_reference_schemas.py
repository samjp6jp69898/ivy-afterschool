"""BACKEND-114：參考資料 schemas 與 ReferenceSpec。"""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from app.models.reference import ClosedDay, ExamType, School, Subject
from app.schemas.reference import (
    ClosedDayCreateIn,
    ClosedDayOut,
    ClosedDayUpdateIn,
    DeleteResultOut,
    NamedItemCreateIn,
    NamedItemOut,
    NamedItemUpdateIn,
    ReferenceListQuery,
    SchoolCreateIn,
    SchoolUpdateIn,
)
from app.services.reference_specs import SPECS, ReferenceSpec


def test_reference_schemas_named_item() -> None:
    with pytest.raises(ValidationError):
        NamedItemCreateIn(name="")
    with pytest.raises(ValidationError):
        NamedItemCreateIn(name="數" * 21)
    with pytest.raises(ValidationError):
        NamedItemUpdateIn.model_validate({})
    created = NamedItemCreateIn(name=" 數學 ")
    assert (created.name, created.sort_order, created.is_active) == ("數學", 0, True)
    with pytest.raises(ValidationError):
        NamedItemCreateIn(name="數學", sort_order=-1)
    assert NamedItemCreateIn(name="數" * 20).name == "數" * 20


def test_reference_schemas_school() -> None:
    assert SchoolCreateIn(name="某某國小").short_name is None
    with pytest.raises(ValidationError):
        SchoolCreateIn(name="校" * 51)
    with pytest.raises(ValidationError):
        SchoolCreateIn(name="某某國小", short_name="簡" * 21)
    with pytest.raises(ValidationError):
        SchoolCreateIn(name="某某國小", short_name="")
    with pytest.raises(ValidationError):
        SchoolUpdateIn.model_validate({})
    assert SchoolUpdateIn.model_validate({"short_name": None}).short_name is None


def test_reference_schemas_closed_day() -> None:
    with pytest.raises(ValidationError) as exc:
        ClosedDayUpdateIn.model_validate({"date": "2026-10-10"})
    assert exc.value.errors()[0]["type"] == "extra_forbidden"
    created = ClosedDayCreateIn(date=date(2026, 10, 10), reason="國慶日")
    assert (created.date, created.reason) == (date(2026, 10, 10), "國慶日")
    with pytest.raises(ValidationError):
        ClosedDayCreateIn(date=date(2026, 10, 10), reason="a" * 101)
    with pytest.raises(ValidationError):
        ClosedDayUpdateIn.model_validate({})


def test_reference_schemas_query_and_out() -> None:
    q = ReferenceListQuery()
    assert (q.active_only, q.date_from, q.date_to) == (False, None, None)
    with pytest.raises(ValidationError):
        ReferenceListQuery.model_validate({"x": 1})
    assert DeleteResultOut(deleted=False, deactivated=True).model_dump() == {
        "deleted": False,
        "deactivated": True,
    }
    assert set(NamedItemOut.model_fields) == {"id", "name", "sort_order", "is_active"}
    assert set(ClosedDayOut.model_fields) == {"id", "date", "reason"}


def test_reference_specs() -> None:
    assert set(SPECS) == {"subjects", "exam-types", "schools", "closed-days"}
    assert SPECS["closed-days"].deactivate_when_referenced is False
    assert SPECS["subjects"].conflict_code == "subject_name_taken"


def test_reference_specs_full_configuration() -> None:
    expected = {
        "subjects": (Subject, "subject_not_found", "subject_name_taken", True),
        "exam-types": (ExamType, "exam_type_not_found", "exam_type_name_taken", True),
        "schools": (School, "school_not_found", "school_name_taken", True),
        "closed-days": (ClosedDay, "closed_day_not_found", "closed_day_exists", False),
    }
    for resource, (model, not_found, conflict, deactivate) in expected.items():
        spec = SPECS[resource]
        assert isinstance(spec, ReferenceSpec)
        assert spec.resource == resource
        assert spec.model is model
        assert spec.not_found_code == not_found
        assert spec.conflict_code == conflict
        assert spec.deactivate_when_referenced is deactivate
    assert SPECS["subjects"].create_schema is NamedItemCreateIn
    assert SPECS["exam-types"].out_schema is NamedItemOut
    assert SPECS["closed-days"].update_schema is ClosedDayUpdateIn
    assert SPECS["closed-days"].out_schema is ClosedDayOut


def test_reference_specs_order_by() -> None:
    def cols(resource: str) -> list[str]:
        return [str(c) for c in SPECS[resource].order_by]

    assert cols("subjects") == ["subjects.sort_order", "subjects.name"]
    assert cols("exam-types") == ["exam_types.sort_order", "exam_types.name"]
    assert cols("schools") == ["schools.name"]
    assert cols("closed-days") == ["closed_days.date DESC"]


def test_reference_specs_are_immutable() -> None:
    with pytest.raises(AttributeError):
        SPECS["subjects"].resource = "schools"  # type: ignore[misc]


def test_reference_schemas_sort_order_upper_bound() -> None:
    assert NamedItemCreateIn(name="數學", sort_order=2147483647).sort_order == 2147483647
    with pytest.raises(ValidationError):
        NamedItemCreateIn(name="數學", sort_order=2147483648)
    assert NamedItemUpdateIn.model_validate({"sort_order": 2147483647}).sort_order == 2147483647
    with pytest.raises(ValidationError):
        NamedItemUpdateIn.model_validate({"sort_order": 2147483648})
