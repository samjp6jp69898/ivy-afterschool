"""BACKEND-148：學生模組 schemas。"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.students import (
    ClassBrief,
    PhotoUploadOut,
    SchoolBrief,
    StudentCreateIn,
    StudentDetailOut,
    StudentListItemOut,
    StudentListQuery,
    StudentPurgeIn,
    StudentPurgeOut,
    StudentSensitiveOut,
    StudentUpdateIn,
)


def _create(**overrides: Any) -> StudentCreateIn:
    data: dict[str, Any] = {"student_no": "S001", "name": "王小明", "grade_level": 3}
    data.update(overrides)
    return StudentCreateIn.model_validate(data)


def _list_item_kwargs(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": uuid4(),
        "student_no": "S001",
        "name": "王小明",
        "gender": None,
        "grade_level": 3,
        "school": None,
        "school_class": None,
        "status": "active",
        "archived_at": None,
    }
    data.update(overrides)
    return data


def test_student_schemas_formats() -> None:
    for bad_no in ("S 001", "", "S" * 21, "學號1", "S_001"):
        with pytest.raises(ValidationError):
            _create(student_no=bad_no)
    for bad in (
        {"grade_level": 7},
        {"grade_level": 0},
        {"status": "graduated"},
        {"gender": "x"},
        {"name": ""},
        {"name": "   "},
        {"name": "名" * 51},
        {"school_class": "班" * 21},
        {"note": "x" * 501},
        {"id_number": "A" * 21},
        {"health_note": "x" * 1001},
        {"extra_field": 1},
        {"name": "王\x00明"},
    ):
        with pytest.raises(ValidationError):
            _create(**bad)

    ok = _create(student_no="S-001", name=" 王小明 ", grade_level=6, gender="female")
    assert ok.name == "王小明"
    assert ok.student_no == "S-001"
    assert ok.status == "active"
    assert ok.gender == "female"
    assert ok.id_number is None
    assert ok.health_note is None
    assert _create(student_no="A" * 20).student_no == "A" * 20
    assert _create(note="x" * 500, health_note="y" * 1000, id_number="A" * 20).note == "x" * 500


def test_student_schemas_update_null_vs_unset() -> None:
    cleared = StudentUpdateIn.model_validate({"id_number": None})
    assert cleared.model_fields_set == {"id_number"}
    assert cleared.id_number is None

    note_only = StudentUpdateIn.model_validate({"note": "x"})
    assert note_only.model_fields_set == {"note"}
    assert "id_number" not in note_only.model_fields_set

    cleared_health = StudentUpdateIn.model_validate({"health_note": None, "class_id": None})
    assert cleared_health.model_fields_set == {"health_note", "class_id"}

    with pytest.raises(ValidationError):
        StudentUpdateIn.model_validate({})


@pytest.mark.parametrize("field", ["name", "student_no", "grade_level", "status"])
def test_student_schemas_update_required_fields_cannot_be_null(field: str) -> None:
    with pytest.raises(ValidationError):
        StudentUpdateIn.model_validate({field: None})


def test_student_schemas_update_limits_and_extra() -> None:
    with pytest.raises(ValidationError):
        StudentUpdateIn.model_validate({"grade_level": 7})
    with pytest.raises(ValidationError):
        StudentUpdateIn.model_validate({"student_no": "bad no"})
    with pytest.raises(ValidationError):
        StudentUpdateIn.model_validate({"note": "x", "unknown": 1})
    with pytest.raises(ValidationError):
        StudentUpdateIn.model_validate({"health_note": "x" * 1001})


def test_student_schemas_list_query() -> None:
    default = StudentListQuery()
    assert default.include_archived is False
    assert default.q is None

    cid = uuid4()
    q = StudentListQuery(q=" 王 ", class_id=cid, grade_level=2, status="suspended")
    assert q.q == "王"
    assert q.class_id == cid
    assert q.status == "suspended"

    for bad in ({"q": "x" * 51}, {"grade_level": 7}, {"status": "gone"}, {"foo": 1}):
        with pytest.raises(ValidationError):
            StudentListQuery.model_validate(bad)


def test_student_schemas_class_alias() -> None:
    u = uuid4()
    item = StudentListItemOut(**_list_item_kwargs(class_=ClassBrief(id=u, name="A 班")))

    dumped = item.model_dump(by_alias=True)
    assert dumped["class"] == {"id": u, "name": "A 班"}
    assert "class_" not in dumped
    # JSON 輸入端也用 class
    again = StudentListItemOut.model_validate(
        {**_list_item_kwargs(), "class": {"id": u, "name": "A 班"}}
    )
    assert again.class_ == ClassBrief(id=u, name="A 班")
    assert StudentListItemOut(**_list_item_kwargs(class_=None)).class_ is None


def test_student_schemas_detail_out() -> None:
    u = uuid4()
    detail = StudentDetailOut(
        **_list_item_kwargs(
            school=SchoolBrief(id=u, name="新生國小", short_name="新生"),
            class_=ClassBrief(id=u, name="A 班"),
        ),
        birthday=date(2016, 5, 1),
        enrolled_on=date(2026, 9, 1),
        withdrawn_on=None,
        note=None,
        photo_url=None,
        has_id_number=True,
        has_health_note=False,
        sensitive=None,
        guardians=[],
    )

    body = detail.model_dump(by_alias=True, mode="json")
    assert body["class"]["name"] == "A 班"
    assert body["school"]["short_name"] == "新生"
    assert body["sensitive"] is None
    assert body["has_id_number"] is True
    assert body["guardians"] == []

    with_sensitive = detail.model_copy(
        update={"sensitive": StudentSensitiveOut(id_number="A123456789", health_note="花生過敏")}
    )
    assert with_sensitive.model_dump(by_alias=True)["sensitive"] == {
        "id_number": "A123456789",
        "health_note": "花生過敏",
    }


def test_student_schemas_purge_and_photo() -> None:
    assert StudentPurgeIn(confirm_student_no="S001").confirm_student_no == "S001"
    for bad in ("", "S" * 21):
        with pytest.raises(ValidationError):
            StudentPurgeIn(confirm_student_no=bad)
    with pytest.raises(ValidationError):
        StudentPurgeIn.model_validate({"confirm_student_no": "S001", "x": 1})

    sid = uuid4()
    when = datetime(2026, 10, 5, tzinfo=UTC)
    out = StudentPurgeOut(student_id=sid, purged_at=when, anonymized_student_no="PURGED-1")
    assert out.model_dump()["anonymized_student_no"] == "PURGED-1"
    assert PhotoUploadOut(photo_url="https://storage.test/p").photo_url == "https://storage.test/p"
