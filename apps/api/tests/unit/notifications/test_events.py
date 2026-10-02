"""BACKEND-201：通知事件定義（domain_spec M9；DB-029 / DB-031 CHECK 清單須與此一致）。"""

from app.notifications.events import EVENTS, PARENT_LINE_CONFIGURABLE, Event, EventDef

EXPECTED_EVENTS = {
    "attendance.checked_in",
    "leave.created",
    "homework.eta_updated",
    "exam.published",
    "pickup.arrived",
    "attendance.checked_out",
    "homework.done",
    "pickup.requested",
    "binding.completed",
    "pickup.completed",
    "leave.cancelled",
    "pickup.replied",
    "pickup.cancelled",
}

# DB-031 CHECK：家長可關閉 LINE 的 7 個事件（pickup.cancelled、binding.completed 不可關閉）
EXPECTED_PARENT_LINE_CONFIGURABLE = {
    "attendance.checked_in",
    "homework.eta_updated",
    "exam.published",
    "homework.done",
    "attendance.checked_out",
    "pickup.completed",
    "pickup.replied",
}


def test_events_exact() -> None:
    assert {e.value for e in Event} == EXPECTED_EVENTS
    assert set(EVENTS) == set(Event)
    assert all(isinstance(d, EventDef) and d.event is e for e, d in EVENTS.items())
    assert all(d.label.strip() for d in EVENTS.values())


def test_events_parent_line_configurable() -> None:
    assert {e.value for e in PARENT_LINE_CONFIGURABLE} == EXPECTED_PARENT_LINE_CONFIGURABLE
    assert {e for e, d in EVENTS.items() if d.parent_line_configurable} == PARENT_LINE_CONFIGURABLE
    assert Event.PICKUP_CANCELLED not in PARENT_LINE_CONFIGURABLE
    assert Event.BINDING_COMPLETED not in PARENT_LINE_CONFIGURABLE
    # 只有家長收件的事件才可能開放設定
    assert all("parent" in EVENTS[e].recipient_types for e in PARENT_LINE_CONFIGURABLE)


def test_events_recipient_types() -> None:
    staff_events = {e.value for e, d in EVENTS.items() if "staff" in d.recipient_types}
    parent_events = {e.value for e, d in EVENTS.items() if "parent" in d.recipient_types}

    assert staff_events == {
        "leave.created",
        "leave.cancelled",
        "pickup.requested",
        "pickup.arrived",
        "pickup.cancelled",
    }
    assert parent_events == EXPECTED_EVENTS - {
        "leave.created",
        "leave.cancelled",
        "pickup.requested",
        "pickup.arrived",
    }
    assert EVENTS[Event.PICKUP_CANCELLED].recipient_types == frozenset({"staff", "parent"})
    assert all(d.recipient_types for d in EVENTS.values())


def test_events_required_payload() -> None:
    assert EVENTS[Event.PICKUP_COMPLETED].required_payload == (
        "student_id",
        "student_name",
        "request_id",
        "time",
        "picked_up_by",
    )
    assert EVENTS[Event.ATTENDANCE_CHECKED_IN].required_payload == (
        "student_id",
        "student_name",
        "time",
    )
    assert EVENTS[Event.LEAVE_CREATED].required_payload == (
        "student_id",
        "student_name",
        "leave_id",
        "start_date",
        "end_date",
        "leave_type_label",
    )
    assert EVENTS[Event.HOMEWORK_ETA_UPDATED].required_payload == (
        "student_id",
        "student_name",
        "ready_eta",
    )
    assert EVENTS[Event.PICKUP_REQUESTED].required_payload == (
        "student_id",
        "student_name",
        "request_id",
    )
    assert EVENTS[Event.PICKUP_REPLIED].required_payload == (
        "student_id",
        "student_name",
        "request_id",
        "reply_message",
    )
    assert EVENTS[Event.PICKUP_CANCELLED].required_payload == (
        "student_id",
        "student_name",
        "request_id",
        "cancelled_by_label",
    )
    assert EVENTS[Event.EXAM_PUBLISHED].required_payload == (
        "student_id",
        "student_name",
        "exam_id",
        "exam_name",
    )
    for event in (Event.HOMEWORK_DONE, Event.BINDING_COMPLETED):
        assert EVENTS[event].required_payload == ("student_id", "student_name")
    # 每個事件至少要能辨識學生
    assert all(d.required_payload[:2] == ("student_id", "student_name") for d in EVENTS.values())
