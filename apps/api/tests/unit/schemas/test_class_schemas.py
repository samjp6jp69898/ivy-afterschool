"""BACKEND-134：班級 schemas。"""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.classes import (
    ClassCreateIn,
    ClassListQuery,
    ClassOut,
    ClassStaffOut,
    ClassStaffPutIn,
    ClassUpdateIn,
)


def test_class_schemas_grade_levels() -> None:
    with pytest.raises(ValidationError):
        ClassCreateIn(name="A", grade_levels=[], academic_year=115)
    with pytest.raises(ValidationError):
        ClassCreateIn(name="A", grade_levels=[0], academic_year=115)
    with pytest.raises(ValidationError):
        ClassCreateIn(name="A", grade_levels=[7], academic_year=115)
    with pytest.raises(ValidationError):
        ClassCreateIn(name="A", grade_levels=[2, 1, 2], academic_year=115)
    assert ClassCreateIn(name="A", grade_levels=[2, 1], academic_year=115).grade_levels == [1, 2]


def test_class_schemas_name_year_sort_order() -> None:
    with pytest.raises(ValidationError):
        ClassCreateIn(name="", grade_levels=[1], academic_year=115)
    with pytest.raises(ValidationError):
        ClassCreateIn(name="班" * 31, grade_levels=[1], academic_year=115)
    for year in (99, 201):
        with pytest.raises(ValidationError):
            ClassCreateIn(name="A", grade_levels=[1], academic_year=year)
    ok = ClassCreateIn(name="A", grade_levels=[1], academic_year=100)
    assert ok.sort_order == 0
    with pytest.raises(ValidationError):
        ClassCreateIn(name="A", grade_levels=[1], academic_year=115, sort_order=-1)


def test_class_schemas_update_requires_field() -> None:
    with pytest.raises(ValidationError):
        ClassUpdateIn.model_validate({})
    assert ClassUpdateIn.model_validate({"grade_levels": [3, 1]}).grade_levels == [1, 3]
    with pytest.raises(ValidationError):
        ClassUpdateIn.model_validate({"grade_levels": []})
    with pytest.raises(ValidationError):
        ClassUpdateIn.model_validate({"name": None})


def test_class_schemas_staff_put_unique() -> None:
    u = uuid4()
    with pytest.raises(ValidationError):
        ClassStaffPutIn(
            items=[
                {"staff_user_id": u, "role": "lead"},
                {"staff_user_id": u, "role": "assistant"},
            ]
        )
    assert ClassStaffPutIn(items=[]).items == []
    with pytest.raises(ValidationError):
        ClassStaffPutIn(items=[{"staff_user_id": uuid4(), "role": "boss"}])


def test_class_schemas_staff_put_max_20() -> None:
    items = [{"staff_user_id": uuid4(), "role": "assistant"} for _ in range(21)]
    with pytest.raises(ValidationError):
        ClassStaffPutIn(items=items)
    assert len(ClassStaffPutIn(items=items[:20]).items) == 20


def test_class_schemas_query_defaults_and_extra() -> None:
    q = ClassListQuery()
    assert (q.academic_year, q.include_archived, q.mine) == (None, False, False)
    with pytest.raises(ValidationError):
        ClassListQuery.model_validate({"x": 1})


def test_class_schemas_out() -> None:
    out = ClassOut(
        id=uuid4(),
        name="A 班",
        grade_levels=[1, 2],
        academic_year=115,
        sort_order=0,
        archived_at=None,
        student_count=12,
        staff=[ClassStaffOut(staff_user_id=uuid4(), display_name="王老師", role="lead")],
    )
    assert out.staff[0].role == "lead"
    assert out.student_count == 12
