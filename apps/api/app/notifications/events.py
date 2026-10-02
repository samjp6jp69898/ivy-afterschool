"""BACKEND-201：通知事件定義（domain_spec M9）。

- 13 個事件與 DB-029 ``ck_notifications_event`` 一致；``PARENT_LINE_CONFIGURABLE`` 與
  DB-031 ``ck_notification_preferences_event`` 的 7 個事件一致。
- ``required_payload`` 是營運模組呼叫 enqueue 時必須提供的 payload key
  （缺 key → enqueue 拋 ValueError）；可選 key 寫在 ``optional_payload``。
- 新增事件時：改本檔、domain_spec M9、DB-029（必要時 DB-031）的 CHECK 以新 migration 擴充。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Literal

RecipientType = Literal["staff", "parent"]

_STAFF: frozenset[RecipientType] = frozenset({"staff"})
_PARENT: frozenset[RecipientType] = frozenset({"parent"})
_BOTH: frozenset[RecipientType] = frozenset({"staff", "parent"})


class Event(StrEnum):
    ATTENDANCE_CHECKED_IN = "attendance.checked_in"
    ATTENDANCE_CHECKED_OUT = "attendance.checked_out"
    LEAVE_CREATED = "leave.created"
    LEAVE_CANCELLED = "leave.cancelled"
    HOMEWORK_ETA_UPDATED = "homework.eta_updated"
    HOMEWORK_DONE = "homework.done"
    PICKUP_REQUESTED = "pickup.requested"
    PICKUP_REPLIED = "pickup.replied"
    PICKUP_ARRIVED = "pickup.arrived"
    PICKUP_COMPLETED = "pickup.completed"
    PICKUP_CANCELLED = "pickup.cancelled"
    EXAM_PUBLISHED = "exam.published"
    BINDING_COMPLETED = "binding.completed"


@dataclass(frozen=True)
class EventDef:
    event: Event
    recipient_types: frozenset[RecipientType]
    label: str
    required_payload: tuple[str, ...]
    parent_line_configurable: bool
    optional_payload: tuple[str, ...] = field(default=())


_DEFS: tuple[EventDef, ...] = (
    EventDef(
        Event.ATTENDANCE_CHECKED_IN,
        _PARENT,
        "到班通知",
        ("student_id", "student_name", "time"),
        parent_line_configurable=True,
    ),
    EventDef(
        Event.ATTENDANCE_CHECKED_OUT,
        _PARENT,
        "離班通知",
        ("student_id", "student_name", "time"),
        parent_line_configurable=True,
    ),
    EventDef(
        Event.LEAVE_CREATED,
        _STAFF,
        "學生請假",
        ("student_id", "student_name", "leave_id", "start_date", "end_date", "leave_type_label"),
        parent_line_configurable=False,
    ),
    EventDef(
        Event.LEAVE_CANCELLED,
        _STAFF,
        "學生取消請假",
        ("student_id", "student_name", "leave_id", "start_date", "end_date", "leave_type_label"),
        parent_line_configurable=False,
    ),
    EventDef(
        Event.HOMEWORK_ETA_UPDATED,
        _PARENT,
        "預計可接送時間",
        ("student_id", "student_name", "ready_eta"),
        parent_line_configurable=True,
        optional_payload=("note",),
    ),
    EventDef(
        Event.HOMEWORK_DONE,
        _PARENT,
        "作業完成",
        ("student_id", "student_name"),
        parent_line_configurable=True,
    ),
    EventDef(
        Event.PICKUP_REQUESTED,
        _STAFF,
        "家長發起接送",
        ("student_id", "student_name", "request_id"),
        parent_line_configurable=False,
        optional_payload=("expected_arrival_at",),
    ),
    EventDef(
        Event.PICKUP_REPLIED,
        _PARENT,
        "老師回覆接送",
        ("student_id", "student_name", "request_id", "reply_message"),
        parent_line_configurable=True,
    ),
    EventDef(
        Event.PICKUP_ARRIVED,
        _STAFF,
        "家長已抵達",
        ("student_id", "student_name", "request_id"),
        parent_line_configurable=False,
    ),
    EventDef(
        Event.PICKUP_COMPLETED,
        _PARENT,
        "接送完成",
        ("student_id", "student_name", "request_id", "time", "picked_up_by"),
        parent_line_configurable=True,
    ),
    EventDef(
        Event.PICKUP_CANCELLED,
        _BOTH,  # 家長取消 → 員工；員工取消 → 家長，由取消者決定收件人
        "接送取消",
        ("student_id", "student_name", "request_id", "cancelled_by_label"),
        parent_line_configurable=False,
        optional_payload=("reason",),
    ),
    EventDef(
        Event.EXAM_PUBLISHED,
        _PARENT,
        "成績公布",
        ("student_id", "student_name", "exam_id", "exam_name"),
        parent_line_configurable=True,
    ),
    EventDef(
        Event.BINDING_COMPLETED,
        _PARENT,
        "綁定完成",
        ("student_id", "student_name"),
        parent_line_configurable=False,
    ),
)

EVENTS: Mapping[Event, EventDef] = MappingProxyType({d.event: d for d in _DEFS})

PARENT_LINE_CONFIGURABLE: frozenset[Event] = frozenset(
    d.event for d in _DEFS if d.parent_line_configurable
)
