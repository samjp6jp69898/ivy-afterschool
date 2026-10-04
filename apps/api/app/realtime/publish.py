"""BACKEND-224：營運模組推送即時更新的唯一入口（domain_spec M7、§2 WebSocket）。

移植 ivy ``services/ws/dismissal.py::schedule_broadcast_threadsafe`` 的「commit 後才廣播」；頻道
改為 topic / 個人 / 學生三類。

- 頻道：``admin:<topic>``（後台看板）、``staff:<id>`` / ``parent:<id>``（個人通知）、
  ``student:<id>``（家長端該小孩的 pickup / homework / attendance 事件）。
- 信封：``{"type", "data", "sent_at"}``，data 經 ``jsonable_encoder`` 轉 JSON 相容（UUID /
  datetime → 字串），``sent_at`` 為 ISO 8601 UTC（秒）。
- ``type`` 必須以 ``f"{topic}."`` 開頭，否則 ValueError（啟動期就能發現拼錯）。
- 家長版資料（``parent_data``）由呼叫端裁切（不含員工姓名、內部備註），只送 student channel；admin
  channel 收完整 data。``parent_data`` 給了但 topic 不在 ``PARENT_VISIBLE_TOPICS`` → ValueError。
- 全部經 BACKEND-006 ``run_after_commit`` 註冊：commit 後才送、rollback 不送。盡力而為：送不到不
  重試（前端斷線以輪詢補齊）。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final, Literal
from uuid import UUID

from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session

from app.core.clock import Clock
from app.core.tx_hooks import run_after_commit
from app.notifications.recipients import Recipient
from app.realtime.broadcaster import publish_threadsafe

Topic = Literal["pickup", "homework", "attendance"]
PARENT_VISIBLE_TOPICS: Final = frozenset({"pickup", "homework", "attendance"})
_SENT_AT_FORMAT: Final = "%Y-%m-%dT%H:%M:%SZ"


def admin_topic_channel(topic: Topic) -> str:
    return f"admin:{topic}"


def staff_channel(staff_id: UUID) -> str:
    return f"staff:{staff_id}"


def parent_channel(parent_id: UUID) -> str:
    return f"parent:{parent_id}"


def student_channel(student_id: UUID) -> str:
    return f"student:{student_id}"


def recipient_channel(recipient: Recipient) -> str:
    return (
        staff_channel(recipient.id) if recipient.type == "staff" else parent_channel(recipient.id)
    )


def envelope(type: str, data: Mapping[str, Any], *, clock: Clock) -> dict[str, Any]:
    if not type:
        raise ValueError("WS 訊息的 type 不可為空")
    return {
        "type": type,
        "data": jsonable_encoder(dict(data)),
        "sent_at": clock.now().strftime(_SENT_AT_FORMAT),
    }


def _check_type(topic: Topic, type: str) -> None:
    if not type.startswith(f"{topic}."):
        raise ValueError(f"WS 訊息 type 必須以 {topic}. 開頭：{type!r}")


def _check_parent_visible(topic: Topic) -> None:
    if topic not in PARENT_VISIBLE_TOPICS:
        raise ValueError(f"topic {topic!r} 不可推送給家長")


def _schedule(session: Session, channels: list[str], message: dict[str, Any]) -> None:
    run_after_commit(session, lambda: publish_threadsafe(channels, message))


def broadcast_after_commit(
    session: Session,
    *,
    topic: Topic,
    type: str,
    data: Mapping[str, Any],
    clock: Clock,
    student_id: UUID | None = None,
    parent_data: Mapping[str, Any] | None = None,
) -> None:
    """commit 後送 admin:<topic>；student_id 與 parent_data 皆有值時另送 student:<id> 裁切版。"""
    _check_type(topic, type)
    if parent_data is not None:
        _check_parent_visible(topic)
    _schedule(session, [admin_topic_channel(topic)], envelope(type, data, clock=clock))
    if student_id is not None and parent_data is not None:
        _schedule(session, [student_channel(student_id)], envelope(type, parent_data, clock=clock))


def push_to_recipient_after_commit(
    session: Session,
    recipient: Recipient,
    type: str,
    data: Mapping[str, Any],
    *,
    clock: Clock,
) -> None:
    """個人通知（站內通知建立等）：只送該收件人的 staff / parent channel。"""
    _schedule(session, [recipient_channel(recipient)], envelope(type, data, clock=clock))


def push_to_student_after_commit(
    session: Session,
    student_id: UUID,
    *,
    topic: Topic,
    type: str,
    data: Mapping[str, Any],
    clock: Clock,
) -> None:
    """只送 student channel（批次操作：admin 端另以一則彙總訊息廣播，家長端逐生推送）。"""
    _check_type(topic, type)
    _check_parent_visible(topic)
    _schedule(session, [student_channel(student_id)], envelope(type, data, clock=clock))
