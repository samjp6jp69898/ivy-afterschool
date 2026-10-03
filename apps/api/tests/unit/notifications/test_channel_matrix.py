"""BACKEND-202：事件 → 收件人類型 → 預設頻道（domain_spec M9）。"""

import pytest

from app.notifications.channel_matrix import CHANNEL_MATRIX, channels_for, check_invariants
from app.notifications.events import EVENTS, Event

# domain_spec M9「頻道」欄逐列對照
_EXPECTED: dict[Event, dict[str, tuple[str, ...]]] = {
    Event.ATTENDANCE_CHECKED_IN: {"parent": ("in_app", "line")},
    Event.ATTENDANCE_CHECKED_OUT: {"parent": ("in_app", "line")},
    Event.LEAVE_CREATED: {"staff": ("in_app", "ws")},
    Event.LEAVE_CANCELLED: {"staff": ("in_app", "ws")},
    Event.HOMEWORK_ETA_UPDATED: {"parent": ("in_app", "line")},
    Event.HOMEWORK_DONE: {"parent": ("in_app", "line")},
    Event.PICKUP_REQUESTED: {"staff": ("in_app", "ws")},
    Event.PICKUP_REPLIED: {"parent": ("in_app", "line")},
    Event.PICKUP_ARRIVED: {"staff": ("ws",)},
    Event.PICKUP_COMPLETED: {"parent": ("in_app", "line")},
    Event.PICKUP_CANCELLED: {"staff": ("in_app", "ws"), "parent": ("in_app", "line")},
    Event.EXAM_PUBLISHED: {"parent": ("in_app", "line")},
    Event.BINDING_COMPLETED: {"parent": ("in_app",)},
}


def test_channel_matrix_values() -> None:
    assert channels_for(Event.PICKUP_ARRIVED, "staff") == ("ws",)
    assert channels_for(Event.LEAVE_CREATED, "staff") == ("in_app", "ws")
    assert channels_for(Event.BINDING_COMPLETED, "parent") == ("in_app",)
    assert channels_for(Event.EXAM_PUBLISHED, "parent") == ("in_app", "line")
    assert channels_for(Event.PICKUP_CANCELLED, "staff") == ("in_app", "ws")
    assert channels_for(Event.PICKUP_CANCELLED, "parent") == ("in_app", "line")
    with pytest.raises(ValueError, match=r"exam\.published"):
        channels_for(Event.EXAM_PUBLISHED, "staff")
    with pytest.raises(ValueError, match=r"pickup\.arrived"):
        channels_for(Event.PICKUP_ARRIVED, "parent")


def test_channel_matrix_matches_domain_spec_table() -> None:
    actual = {event: dict(by_type) for event, by_type in CHANNEL_MATRIX.items()}

    assert actual == _EXPECTED
    for event, by_type in _EXPECTED.items():
        for recipient_type, channels in by_type.items():
            assert channels_for(event, recipient_type) == channels  # type: ignore[arg-type]


def test_channel_matrix_complete() -> None:
    assert set(CHANNEL_MATRIX) == set(Event)


def test_channel_matrix_invariants() -> None:
    for event, by_type in CHANNEL_MATRIX.items():
        assert set(by_type) == set(EVENTS[event].recipient_types), event
        for recipient_type, channels in by_type.items():
            assert channels, (event, recipient_type)
            assert len(set(channels)) == len(channels), (event, recipient_type)
            if "line" in channels:
                assert "in_app" in channels, (event, recipient_type)
                assert recipient_type == "parent", (event, recipient_type)


def test_channel_matrix_is_read_only() -> None:
    with pytest.raises(TypeError):
        CHANNEL_MATRIX[Event.HOMEWORK_DONE] = {}  # type: ignore[index]
    with pytest.raises(TypeError):
        CHANNEL_MATRIX[Event.HOMEWORK_DONE]["parent"] = ("in_app",)  # type: ignore[index]


def _matrix_with(
    event: Event, by_type: dict[str, tuple[str, ...]]
) -> dict[Event, dict[str, tuple[str, ...]]]:
    return {**_EXPECTED, event: by_type}


@pytest.mark.parametrize(
    ("matrix", "fragment"),
    [
        ({e: v for e, v in _EXPECTED.items() if e != Event.HOMEWORK_DONE}, "homework.done"),
        (_matrix_with(Event.EXAM_PUBLISHED, {"parent": ("line",)}), "in_app"),
        (
            _matrix_with(
                Event.PICKUP_CANCELLED, {"staff": ("in_app", "line"), "parent": ("in_app",)}
            ),
            "parent",
        ),
        (_matrix_with(Event.PICKUP_CANCELLED, {"staff": ("in_app", "ws")}), "recipient_types"),
        (_matrix_with(Event.HOMEWORK_DONE, {"parent": ()}), "空"),
    ],
)
def test_channel_matrix_invariant_checker_rejects_bad_matrix(
    matrix: dict[Event, dict[str, tuple[str, ...]]], fragment: str
) -> None:
    # 模組載入時以同一個函式檢查 CHANNEL_MATRIX；這裡證明它真的會擋
    check_invariants(CHANNEL_MATRIX)
    with pytest.raises(AssertionError, match=fragment):
        check_invariants(matrix)  # type: ignore[arg-type]
