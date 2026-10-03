"""BACKEND-203：事件標題 / 內文 / 深連結渲染與 LINE 文字。"""

from datetime import date
from typing import Any

import pytest

from app.notifications.events import EVENTS, Event
from app.notifications.templates import DEEP_LINKS, Rendered, line_text, render

_BASE = {"student_id": "s-1", "student_name": "王小明"}

# 13 個事件逐字比對（payload 只放該事件需要的 key）
_CASES: list[tuple[Event, dict[str, Any], Rendered]] = [
    (
        Event.ATTENDANCE_CHECKED_IN,
        {**_BASE, "time": "15:20"},
        Rendered("王小明 已到班", "王小明 於 15:20 到達安親班。", "/attendance"),
    ),
    (
        Event.ATTENDANCE_CHECKED_OUT,
        {**_BASE, "time": "18:05"},
        Rendered("王小明 已離班", "王小明 於 18:05 離開安親班。", "/attendance"),
    ),
    (
        Event.LEAVE_CREATED,
        {
            **_BASE,
            "leave_id": "l-1",
            "start_date": "2026-09-01",
            "end_date": "2026-09-02",
            "leave_type_label": "病假",
        },
        Rendered("王小明 請假", "王小明 2026-09-01～2026-09-02 病假。", "/leaves"),
    ),
    (
        Event.LEAVE_CANCELLED,
        {
            **_BASE,
            "leave_id": "l-1",
            "start_date": "2026-09-01",
            "end_date": "2026-09-02",
            "leave_type_label": "事假",
        },
        Rendered(
            "王小明 取消請假",
            "王小明 2026-09-01～2026-09-02 的請假已取消。",
            "/leaves",
        ),
    ),
    (
        Event.HOMEWORK_ETA_UPDATED,
        {**_BASE, "ready_eta": "17:30"},
        Rendered("王小明 預計 17:30 可接送", "預計 17:30 可接送。", "/homework"),
    ),
    (
        Event.HOMEWORK_DONE,
        dict(_BASE),
        Rendered("王小明 作業已完成", "作業已完成，可以來接送了。", "/homework"),
    ),
    (
        Event.PICKUP_REQUESTED,
        {**_BASE, "request_id": "r-1"},
        Rendered("王小明 家長要來接", "家長已發起接送。", "/pickup"),
    ),
    (
        Event.PICKUP_REPLIED,
        {**_BASE, "request_id": "r-1", "reply_message": "還在訂正數學，約 10 分鐘"},
        Rendered("王小明 接送回覆", "還在訂正數學，約 10 分鐘", "/pickup"),
    ),
    (
        Event.PICKUP_ARRIVED,
        {**_BASE, "request_id": "r-1"},
        Rendered("王小明 家長已抵達", "家長已到達門口。", "/pickup"),
    ),
    (
        Event.PICKUP_COMPLETED,
        {**_BASE, "request_id": "r-1", "time": "18:10", "picked_up_by": "王媽媽"},
        Rendered("王小明 已接走", "王小明 於 18:10 由 王媽媽 接走。", "/pickup"),
    ),
    (
        Event.PICKUP_CANCELLED,
        {**_BASE, "request_id": "r-1", "cancelled_by_label": "老師"},
        Rendered("王小明 接送已取消", "老師已取消今天的接送請求。", "/pickup"),
    ),
    (
        Event.EXAM_PUBLISHED,
        {**_BASE, "exam_id": "e-1", "exam_name": "第一次段考"},
        Rendered("第一次段考 成績已公布", "王小明 的 第一次段考 成績已公布。", "/exams"),
    ),
    (
        Event.BINDING_COMPLETED,
        dict(_BASE),
        Rendered("綁定完成", "已成功綁定 王小明，之後會在這裡收到通知。", "/"),
    ),
]


def test_templates_homework_done() -> None:
    result = render(Event.HOMEWORK_DONE, {"student_id": "x", "student_name": "王小明"})

    assert result == Rendered("王小明 作業已完成", "作業已完成，可以來接送了。", "/homework")


@pytest.mark.parametrize(("event", "payload", "expected"), _CASES, ids=[c[0].value for c in _CASES])
def test_templates_exact_copy(event: Event, payload: dict[str, Any], expected: Rendered) -> None:
    assert render(event, payload) == expected


def test_templates_cases_cover_all_events() -> None:
    assert {c[0] for c in _CASES} == set(Event)


def test_templates_eta_with_note() -> None:
    payload = {"student_id": "x", "student_name": "王小明", "ready_eta": "17:30"}

    with_note = render(Event.HOMEWORK_ETA_UPDATED, {**payload, "note": "剩數學訂正"})
    assert with_note.body == "預計 17:30 可接送。\n剩數學訂正"
    assert with_note.title == "王小明 預計 17:30 可接送"

    assert render(Event.HOMEWORK_ETA_UPDATED, payload).body == "預計 17:30 可接送。"
    # None / 空字串視同沒有
    assert (
        render(Event.HOMEWORK_ETA_UPDATED, {**payload, "note": None}).body == "預計 17:30 可接送。"
    )
    assert (
        render(Event.HOMEWORK_ETA_UPDATED, {**payload, "note": "  "}).body == "預計 17:30 可接送。"
    )


def test_templates_pickup_requested_branches() -> None:
    payload = {"student_id": "x", "student_name": "王小明", "request_id": "r"}

    with_eta = render(Event.PICKUP_REQUESTED, {**payload, "expected_arrival_at": "17:45"})
    assert with_eta.body == "家長預計 17:45 抵達。"
    assert render(Event.PICKUP_REQUESTED, payload).body == "家長已發起接送。"
    assert render(Event.PICKUP_REQUESTED, {**payload, "expected_arrival_at": None}).body == (
        "家長已發起接送。"
    )


def test_templates_pickup_cancelled() -> None:
    payload = {
        "student_id": "x",
        "student_name": "王小明",
        "request_id": "r",
        "cancelled_by_label": "家長",
    }

    assert render(Event.PICKUP_CANCELLED, {**payload, "reason": "改由爺爺接"}) == Rendered(
        "王小明 接送已取消", "家長已取消今天的接送請求。\n原因：改由爺爺接", "/pickup"
    )
    assert render(Event.PICKUP_CANCELLED, payload).body == "家長已取消今天的接送請求。"
    assert render(Event.PICKUP_CANCELLED, {**payload, "reason": ""}).body == (
        "家長已取消今天的接送請求。"
    )


def test_templates_missing_payload() -> None:
    with pytest.raises(ValueError, match="request_id") as exc_info:
        render(Event.PICKUP_COMPLETED, {"student_id": "x", "student_name": "王小明"})

    message = str(exc_info.value)
    for key in ("request_id", "time", "picked_up_by"):
        assert key in message
    assert "student_name" not in message


def test_templates_missing_payload_for_every_event() -> None:
    for event, definition in EVENTS.items():
        payload = {key: "v" for key in definition.required_payload}
        dropped = definition.required_payload[-1]
        payload.pop(dropped)
        with pytest.raises(ValueError, match=dropped):
            render(event, payload)


def test_templates_non_string_values_are_stringified() -> None:
    result = render(
        Event.LEAVE_CREATED,
        {
            **_BASE,
            "leave_id": "l-1",
            "start_date": date(2026, 9, 1),
            "end_date": date(2026, 9, 3),
            "leave_type_label": "病假",
        },
    )

    assert result.body == "王小明 2026-09-01～2026-09-03 病假。"


def test_templates_line_text() -> None:
    text = line_text(Rendered("t", "b", "/homework"), public_base_url="https://as.example.com")

    assert text == "t\nb\nhttps://as.example.com/parent/#/homework"


def test_templates_line_text_root_link_and_trailing_slash() -> None:
    rendered = render(Event.BINDING_COMPLETED, dict(_BASE))

    text = line_text(rendered, public_base_url="https://as.example.com/")

    assert text.endswith("\nhttps://as.example.com/parent/#/")


def test_templates_line_text_truncates_body_keeps_link() -> None:
    link = "https://as.example.com/parent/#/homework"

    text = line_text(
        Rendered("t", "b" * 6000, "/homework"), public_base_url="https://as.example.com"
    )

    assert len(text) == 5000
    assert text.startswith("t\nbbb")
    assert text.endswith("…\n" + link)


def test_templates_truncate_title() -> None:
    result = render(Event.HOMEWORK_DONE, {"student_id": "x", "student_name": "王" * 120})

    assert len(result.title) == 100
    assert result.title.endswith("…")
    assert result.title.startswith("王" * 99)


def test_templates_truncate_body() -> None:
    payload = {
        "student_id": "x",
        "student_name": "王小明",
        "request_id": "r",
        "reply_message": "字" * 1200,
    }

    result = render(Event.PICKUP_REPLIED, payload)

    assert len(result.body) == 1000
    assert result.body == "字" * 999 + "…"


def test_templates_no_truncation_at_exact_limit() -> None:
    payload = {
        "student_id": "x",
        "student_name": "王小明",
        "request_id": "r",
        "reply_message": "字" * 1000,
    }

    assert render(Event.PICKUP_REPLIED, payload).body == "字" * 1000


def test_templates_all_events_render() -> None:
    for event, definition in EVENTS.items():
        payload = {key: f"假值{i}" for i, key in enumerate(definition.required_payload)}
        rendered = render(event, payload)
        assert rendered.title.strip(), event
        assert rendered.body.strip(), event
        assert rendered.deep_link == DEEP_LINKS[event]
    assert set(DEEP_LINKS) == set(Event)
