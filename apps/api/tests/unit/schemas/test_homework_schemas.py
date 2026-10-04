"""BACKEND-371：作業進度模組 schemas。"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.homework import (
    BoardOut,
    BoardQuery,
    BoardStudentOut,
    BoardSummaryOut,
    BoardWindowOut,
    HomeworkBatchCreateIn,
    HomeworkBatchOut,
    HomeworkItemCreateIn,
    HomeworkItemOut,
    HomeworkItemUpdateIn,
    HomeworkMutationOut,
    ParentHomeworkItemOut,
    ParentHomeworkOut,
    ProgressOut,
    ProgressPutIn,
)

_NOW = datetime(2026, 9, 1, 8, 0, tzinfo=UTC)


def _item_out(**overrides: Any) -> HomeworkItemOut:
    data: dict[str, Any] = {
        "id": uuid4(),
        "student_id": uuid4(),
        "service_date": date(2026, 9, 1),
        "subject_id": None,
        "subject_name": None,
        "title": "國語第5課生字",
        "status": "todo",
        "sort_order": 0,
        "updated_at": _NOW,
    }
    data.update(overrides)
    return HomeworkItemOut.model_validate(data)


def _progress_out(**overrides: Any) -> ProgressOut:
    data: dict[str, Any] = {
        "student_id": uuid4(),
        "service_date": date(2026, 9, 1),
        "overall_status": "not_started",
        "ready_eta": None,
        "note": None,
        "eta_updated_at": None,
        "eta_updated_by_name": None,
    }
    data.update(overrides)
    return ProgressOut.model_validate(data)


def test_homework_schemas_time_format() -> None:
    assert ProgressPutIn(ready_eta="17:30").ready_eta == "17:30"
    assert ProgressPutIn(ready_eta="00:00").ready_eta == "00:00"
    assert ProgressPutIn(ready_eta="23:59").ready_eta == "23:59"
    for bad in ("7:30", "24:00", "17:60", "1730", "17:30:00", ""):
        with pytest.raises(ValidationError):
            ProgressPutIn(ready_eta=bad)
    assert _progress_out(ready_eta="17:30").ready_eta == "17:30"
    with pytest.raises(ValidationError):
        _progress_out(ready_eta="7:30")


def test_homework_schemas_progress_requires_field() -> None:
    with pytest.raises(ValidationError):
        ProgressPutIn.model_validate({"service_date": "2026-09-01"})
    with pytest.raises(ValidationError):
        ProgressPutIn.model_validate({})

    cleared = ProgressPutIn.model_validate({"ready_eta": None})
    assert cleared.model_fields_set == {"ready_eta"}
    assert cleared.ready_eta is None

    unset = ProgressPutIn.model_validate({"note": "x"})
    assert "ready_eta" not in unset.model_fields_set

    with_date = ProgressPutIn.model_validate({"service_date": "2026-09-01", "note": None})
    assert with_date.service_date == date(2026, 9, 1)
    assert with_date.model_fields_set == {"service_date", "note"}
    assert with_date.note is None

    with pytest.raises(ValidationError):
        ProgressPutIn(note="x" * 201)
    with pytest.raises(ValidationError):
        ProgressPutIn.model_validate({"note": "x", "unknown": 1})


def test_homework_schemas_update_and_batch() -> None:
    with pytest.raises(ValidationError):
        HomeworkItemUpdateIn.model_validate({})
    with pytest.raises(ValidationError):
        HomeworkItemUpdateIn.model_validate({"title": None})
    with pytest.raises(ValidationError):
        HomeworkItemUpdateIn.model_validate({"status": "finished"})
    with pytest.raises(ValidationError):
        HomeworkItemUpdateIn.model_validate({"sort_order": -1})
    cleared = HomeworkItemUpdateIn.model_validate({"subject_id": None})
    assert cleared.model_fields_set == {"subject_id"}
    assert HomeworkItemUpdateIn.model_validate({"status": "correcting"}).status == "correcting"

    c, u = uuid4(), uuid4()
    with pytest.raises(ValidationError):
        HomeworkBatchCreateIn(class_id=c, title="國語第5課生字", student_ids=[u, u])
    with pytest.raises(ValidationError):
        HomeworkBatchCreateIn(class_id=c, title="")
    with pytest.raises(ValidationError):
        HomeworkBatchCreateIn(class_id=c, title="x" * 101)
    with pytest.raises(ValidationError):
        HomeworkBatchCreateIn(class_id=c, title="a", student_ids=[])
    with pytest.raises(ValidationError):
        HomeworkBatchCreateIn(class_id=c, title="a", student_ids=[uuid4() for _ in range(101)])
    ok = HomeworkBatchCreateIn(class_id=c, title="a", student_ids=[uuid4() for _ in range(100)])
    assert ok.student_ids is not None
    assert len(ok.student_ids) == 100
    whole_class = HomeworkBatchCreateIn(class_id=c, title="a")
    assert whole_class.student_ids is None
    assert whole_class.service_date is None
    assert whole_class.subject_id is None


def test_homework_schemas_overall_values() -> None:
    with pytest.raises(ValidationError):
        ProgressPutIn.model_validate({"overall": "in_progress"})
    assert ProgressPutIn(overall="done").overall == "done"
    assert ProgressPutIn(overall="auto").overall == "auto"


def test_homework_schemas_item_create() -> None:
    sid = uuid4()
    item = HomeworkItemCreateIn(student_id=sid, title=" 數學習作 ")
    assert item.title == "數學習作"
    assert item.status == "todo"
    assert item.sort_order == 0
    assert item.service_date is None
    assert item.subject_id is None
    for bad in (
        {"title": ""},
        {"title": "x" * 101},
        {"status": "nope"},
        {"sort_order": -1},
        {"sort_order": 2147483648},
        {"extra": 1},
    ):
        with pytest.raises(ValidationError):
            HomeworkItemCreateIn.model_validate({"student_id": sid, "title": "a", **bad})


def test_homework_schemas_output_models() -> None:
    sid = uuid4()
    progress = _progress_out(student_id=sid, overall_status="in_progress", ready_eta="18:00")
    item = _item_out(student_id=sid, status="doing")

    assert HomeworkMutationOut(item=None, progress=progress).item is None
    assert HomeworkBatchOut(created=1, items=[item]).created == 1

    student = BoardStudentOut(
        student_id=sid,
        student_no="S001",
        name="王小明",
        class_id=None,
        class_name=None,
        attendance_status="present",
        items=[item],
        progress=progress,
    )
    board = BoardOut(
        date=date(2026, 9, 1),
        window=BoardWindowOut(past_days=7, future_days=14),
        summary=BoardSummaryOut(total=1, done=0, in_progress=1, not_started=0),
        students=[student],
    )
    dumped = board.model_dump(mode="json")
    assert dumped["students"][0]["items"][0]["status"] == "doing"
    assert dumped["window"] == {"past_days": 7, "future_days": 14}
    with pytest.raises(ValidationError):
        _item_out(status="finished")
    with pytest.raises(ValidationError):
        _progress_out(overall_status="in_review")

    q = BoardQuery()
    assert (q.date, q.class_id) == (None, None)
    with pytest.raises(ValidationError):
        BoardQuery.model_validate({"foo": 1})


def test_homework_schemas_parent_output() -> None:
    sid = uuid4()
    parent = ParentHomeworkOut(
        student_id=sid,
        date=date(2026, 9, 1),
        items=[ParentHomeworkItemOut(title="國語", subject_name="國語", status="done")],
        overall_status="done",
        ready_eta=None,
        note=None,
        updated_at=None,
    )
    body = parent.model_dump(mode="json")
    assert set(body["items"][0]) == {"title", "subject_name", "status"}
    assert body["updated_at"] is None
