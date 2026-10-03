"""BACKEND-203：事件 → 標題 / 內文 / 深連結（站內通知與 LINE 推播共用）。

移植 ivy ``services/notification/renderers.py`` 的「事件 → 文案」集中管理；文案為本專案固定字串。

- 缺 ``EVENTS[event].required_payload`` 任一 key → ValueError（列出全部缺少的 key）。
- 可選欄位（note / expected_arrival_at / reason）為 None 或空白時視同沒有。
- title 上限 100、body 上限 1000（DB-029 CHECK），超過截斷並以「…」結尾。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Final

from app.notifications.events import EVENTS, Event

TITLE_MAX: Final = 100
BODY_MAX: Final = 1000
LINE_TEXT_MAX: Final = 5000
_ELLIPSIS: Final = "…"


@dataclass(frozen=True)
class Rendered:
    title: str  # ≤100（DB-029 CHECK）
    body: str  # ≤1000
    deep_link: str  # 家長端 hash 路由（例如 '/homework'）或後台路由（例如 '/pickup'）


DEEP_LINKS: Final[Mapping[Event, str]] = MappingProxyType(
    {
        Event.ATTENDANCE_CHECKED_IN: "/attendance",
        Event.ATTENDANCE_CHECKED_OUT: "/attendance",
        Event.LEAVE_CREATED: "/leaves",
        Event.LEAVE_CANCELLED: "/leaves",
        Event.HOMEWORK_ETA_UPDATED: "/homework",
        Event.HOMEWORK_DONE: "/homework",
        Event.PICKUP_REQUESTED: "/pickup",
        Event.PICKUP_REPLIED: "/pickup",
        Event.PICKUP_ARRIVED: "/pickup",
        Event.PICKUP_COMPLETED: "/pickup",
        Event.PICKUP_CANCELLED: "/pickup",
        Event.EXAM_PUBLISHED: "/exams",
        Event.BINDING_COMPLETED: "/",
    }
)


class _Payload:
    """payload 的字串化存取：必填 key 已檢查過，可選 key 為 None / 空白時回 None。"""

    def __init__(self, raw: Mapping[str, Any]) -> None:
        self._raw = raw

    def __getitem__(self, key: str) -> str:
        return str(self._raw[key])

    def optional(self, key: str) -> str | None:
        value = self._raw.get(key)
        if value is None:
            return None
        text = str(value)
        return text if text.strip() else None


def _eta_updated(p: _Payload) -> tuple[str, str]:
    body = f"預計 {p['ready_eta']} 可接送。"
    if (note := p.optional("note")) is not None:
        body += f"\n{note}"
    return f"{p['student_name']} 預計 {p['ready_eta']} 可接送", body


def _pickup_requested(p: _Payload) -> tuple[str, str]:
    eta = p.optional("expected_arrival_at")
    body = f"家長預計 {eta} 抵達。" if eta is not None else "家長已發起接送。"
    return f"{p['student_name']} 家長要來接", body


def _pickup_cancelled(p: _Payload) -> tuple[str, str]:
    body = f"{p['cancelled_by_label']}已取消今天的接送請求。"
    if (reason := p.optional("reason")) is not None:
        body += f"\n原因：{reason}"
    return f"{p['student_name']} 接送已取消", body


_RENDERERS: Final[Mapping[Event, Callable[[_Payload], tuple[str, str]]]] = MappingProxyType(
    {
        Event.ATTENDANCE_CHECKED_IN: lambda p: (
            f"{p['student_name']} 已到班",
            f"{p['student_name']} 於 {p['time']} 到達安親班。",
        ),
        Event.ATTENDANCE_CHECKED_OUT: lambda p: (
            f"{p['student_name']} 已離班",
            f"{p['student_name']} 於 {p['time']} 離開安親班。",
        ),
        Event.LEAVE_CREATED: lambda p: (
            f"{p['student_name']} 請假",
            f"{p['student_name']} {p['start_date']}～{p['end_date']} {p['leave_type_label']}。",
        ),
        Event.LEAVE_CANCELLED: lambda p: (
            f"{p['student_name']} 取消請假",
            f"{p['student_name']} {p['start_date']}～{p['end_date']} 的請假已取消。",
        ),
        Event.HOMEWORK_ETA_UPDATED: _eta_updated,
        Event.HOMEWORK_DONE: lambda p: (
            f"{p['student_name']} 作業已完成",
            "作業已完成，可以來接送了。",
        ),
        Event.PICKUP_REQUESTED: _pickup_requested,
        Event.PICKUP_REPLIED: lambda p: (f"{p['student_name']} 接送回覆", p["reply_message"]),
        Event.PICKUP_ARRIVED: lambda p: (f"{p['student_name']} 家長已抵達", "家長已到達門口。"),
        Event.PICKUP_COMPLETED: lambda p: (
            f"{p['student_name']} 已接走",
            f"{p['student_name']} 於 {p['time']} 由 {p['picked_up_by']} 接走。",
        ),
        Event.PICKUP_CANCELLED: _pickup_cancelled,
        Event.EXAM_PUBLISHED: lambda p: (
            f"{p['exam_name']} 成績已公布",
            f"{p['student_name']} 的 {p['exam_name']} 成績已公布。",
        ),
        Event.BINDING_COMPLETED: lambda p: (
            "綁定完成",
            f"已成功綁定 {p['student_name']}，之後會在這裡收到通知。",
        ),
    }
)


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - len(_ELLIPSIS)] + _ELLIPSIS


def render(event: Event, payload: Mapping[str, Any]) -> Rendered:
    missing = [key for key in EVENTS[event].required_payload if key not in payload]
    if missing:
        raise ValueError(f"{event} 的 payload 缺少：{', '.join(missing)}")
    title, body = _RENDERERS[event](_Payload(payload))
    return Rendered(
        title=_truncate(title, TITLE_MAX),
        body=_truncate(body, BODY_MAX),
        deep_link=DEEP_LINKS[event],
    )


def line_text(rendered: Rendered, *, public_base_url: str) -> str:
    """``{title}\\n{body}\\n{public_base_url}/parent/#{deep_link}``。

    總長截 5000：先截 title + body（以「…」結尾），連結保持完整。
    """
    link = f"{public_base_url.rstrip('/')}/parent/#{rendered.deep_link}"
    head = f"{rendered.title}\n{rendered.body}"
    budget = LINE_TEXT_MAX - len(link) - 1
    if budget > len(_ELLIPSIS):
        return f"{_truncate(head, budget)}\n{link}"
    # 連結本身就超過上限（不應發生）：整段硬截
    return _truncate(f"{head}\n{link}", LINE_TEXT_MAX)
