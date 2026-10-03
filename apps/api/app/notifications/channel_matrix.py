"""BACKEND-202：事件 → 收件人類型 → 預設頻道（domain_spec M9「頻道」欄）。

移植 ivy ``services/notification/channel_matrix.py::CHANNEL_MATRIX`` 的宣告式 dict。

不變式（模組載入時由 ``check_invariants`` 檢查，違反即 AssertionError，不受 ``python -O`` 影響）：
1. 每個 Event 都在矩陣中，且收件人類型 key 集合恰為 ``EVENTS[e].recipient_types``。
2. 含 ``line`` 的頻道組合必含 ``in_app``（outbox 依附 notifications 列，DB-030）。
3. ``line`` 只出現在 parent 之下（員工沒有 LINE 帳號欄位）。
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final, Literal

from app.notifications.events import EVENTS, Event, RecipientType

Channel = Literal["in_app", "line", "ws"]

_IN_APP_LINE: Final[tuple[Channel, ...]] = ("in_app", "line")
_IN_APP_WS: Final[tuple[Channel, ...]] = ("in_app", "ws")

_MATRIX: dict[Event, dict[RecipientType, tuple[Channel, ...]]] = {
    Event.ATTENDANCE_CHECKED_IN: {"parent": _IN_APP_LINE},
    Event.ATTENDANCE_CHECKED_OUT: {"parent": _IN_APP_LINE},
    Event.LEAVE_CREATED: {"staff": _IN_APP_WS},
    Event.LEAVE_CANCELLED: {"staff": _IN_APP_WS},
    Event.HOMEWORK_ETA_UPDATED: {"parent": _IN_APP_LINE},
    Event.HOMEWORK_DONE: {"parent": _IN_APP_LINE},
    Event.PICKUP_REQUESTED: {"staff": _IN_APP_WS},
    Event.PICKUP_REPLIED: {"parent": _IN_APP_LINE},
    Event.PICKUP_ARRIVED: {"staff": ("ws",)},
    Event.PICKUP_COMPLETED: {"parent": _IN_APP_LINE},
    # 家長取消通知員工；員工取消通知發起的家長
    Event.PICKUP_CANCELLED: {"staff": _IN_APP_WS, "parent": _IN_APP_LINE},
    Event.EXAM_PUBLISHED: {"parent": _IN_APP_LINE},
    Event.BINDING_COMPLETED: {"parent": ("in_app",)},
}

CHANNEL_MATRIX: Final[Mapping[Event, Mapping[RecipientType, tuple[Channel, ...]]]] = (
    MappingProxyType({event: MappingProxyType(by_type) for event, by_type in _MATRIX.items()})
)


def check_invariants(
    matrix: Mapping[Event, Mapping[RecipientType, tuple[Channel, ...]]],
) -> None:
    missing = sorted(set(Event) - set(matrix))
    if missing:
        raise AssertionError(f"CHANNEL_MATRIX 缺少事件：{missing}")
    for event, by_type in matrix.items():
        expected = EVENTS[event].recipient_types
        if set(by_type) != expected:
            raise AssertionError(
                f"{event} 的收件人類型 {sorted(by_type)} "
                f"與 recipient_types {sorted(expected)} 不一致"
            )
        for recipient_type, channels in by_type.items():
            if not channels:
                raise AssertionError(f"{event} / {recipient_type} 的頻道組合是空的")
            if "line" in channels and "in_app" not in channels:
                raise AssertionError(f"{event} / {recipient_type} 含 line 卻沒有 in_app")
            if "line" in channels and recipient_type != "parent":
                raise AssertionError(f"{event} / {recipient_type}：line 只能出現在 parent 之下")


check_invariants(CHANNEL_MATRIX)


def channels_for(event: Event, recipient_type: RecipientType) -> tuple[Channel, ...]:
    by_type = CHANNEL_MATRIX[event]
    if recipient_type not in by_type:
        raise ValueError(f"{event} 不發給 {recipient_type}")
    return by_type[recipient_type]
