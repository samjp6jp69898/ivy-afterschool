"""BACKEND-210：通知 schemas。"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.notifications import (
    MarkAllReadOut,
    NotificationListQuery,
    NotificationOut,
    NotificationPageOut,
    PreferenceItemOut,
    PreferencesOut,
    PreferencesPutIn,
)


def test_notification_schemas_prefs_unique() -> None:
    with pytest.raises(ValidationError):
        PreferencesPutIn(
            items=[
                {"event": "homework.done", "line_enabled": False},
                {"event": "homework.done", "line_enabled": True},
            ]
        )
    with pytest.raises(ValidationError):
        PreferencesPutIn(items=[])


def test_notification_schemas_prefs_size_and_extra() -> None:
    items = [{"event": f"e.{i}", "line_enabled": True} for i in range(8)]
    with pytest.raises(ValidationError):
        PreferencesPutIn(items=items)
    assert len(PreferencesPutIn(items=items[:7]).items) == 7
    with pytest.raises(ValidationError) as exc:
        PreferencesPutIn.model_validate(
            {"items": [{"event": "homework.done", "line_enabled": True, "x": 1}]}
        )
    assert exc.value.errors()[0]["type"] == "extra_forbidden"


def test_notification_schemas_extra() -> None:
    with pytest.raises(ValidationError):
        NotificationListQuery.model_validate({"unread_only": True, "x": 1})
    assert NotificationListQuery().unread_only is False
    assert NotificationListQuery(unread_only=True).unread_only is True


def test_notification_schemas_out() -> None:
    n = NotificationOut(
        id=uuid4(),
        event="homework.done",
        title="作業完成",
        body="王小明已完成作業",
        payload={"student_id": "x"},
        read_at=None,
        created_at=datetime(2026, 9, 1, tzinfo=UTC),
        deep_link="/homework",
    )
    page = NotificationPageOut(items=[n], total=1, unread_count=1)
    assert page.items[0].deep_link == "/homework"
    assert (page.total, page.unread_count) == (1, 1)
    assert MarkAllReadOut(updated=3).updated == 3
    prefs = PreferencesOut(
        items=[PreferenceItemOut(event="homework.done", label="作業完成", line_enabled=True)]
    )
    assert prefs.items[0].label == "作業完成"
