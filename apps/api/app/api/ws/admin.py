"""BACKEND-226：員工 WebSocket ``/api/ws/admin``（個人通知 + 依權限訂閱 pickup / homework /
attendance；domain_spec §2）。

移植 ivy ``api/dismissal_ws.py::admin_dismissal_ws`` 與 ``api/inbox_ws.py::employee_inbox_ws``，
合併為單一連線；去掉 rate limit、tenant、角色白名單，改為 topic 對照權限碼。

1. Origin 已由 BACKEND-019 檢查（外站 → 4403）。``ws_load_staff`` 失敗 → close 4401 / 4403，不
   accept。
2. accept 後自動訂閱 ``staff_channel(staff.id)``（個人通知），回 ``{"type":"ready","topics":[]}``。
3. ``{"action":"subscribe","topics":[...]}``：topic 權限對照 ``TOPIC_PERMISSIONS``；有權限者訂閱
   ``admin_topic_channel``，無權限者回
   ``{"type":"error","code":"permission_denied","topics":[...]}``（有訂上任何一個才另回
   ``subscribed``，內容為目前全部已訂閱 topic，排序）；未知 topic 或格式錯 → ``bad_message``。
   ``unsubscribe`` 取消並回 ``subscribed`` 清單。其他 action → ``bad_message``。
4. 每 ``REVALIDATE_SECONDS`` 秒重新 ``ws_load_staff``；失敗（停用、token_version 變、強制改密碼）→
   close 4401；已失去權限的 topic 自動取消並回
   ``{"type":"unsubscribed","topics":[...],"reason":"permission_revoked"}``。
5. 斷線時 ``broadcaster.unsubscribe(ws)``（全部頻道）。
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends, WebSocket

from app.api.deps import CurrentStaff
from app.api.ws import common as ws_common
from app.api.ws.common import close_code_for, run_in_thread, run_ws_loop, ws_load_staff
from app.core.clock import Clock, get_clock
from app.core.errors import AppError
from app.core.permissions import Permission
from app.realtime.broadcaster import get_broadcaster
from app.realtime.publish import Topic, admin_topic_channel, staff_channel

logger = logging.getLogger(__name__)

router = APIRouter()

TOPIC_PERMISSIONS: Final[dict[Topic, Permission]] = {
    "pickup": Permission.PICKUP_READ,
    "homework": Permission.HOMEWORK_READ,
    "attendance": Permission.ATTENDANCE_READ,
}
# 測試以 monkeypatch 縮短；執行期才讀取模組屬性
REVALIDATE_SECONDS: float = ws_common.REVALIDATE_SECONDS

_BAD_MESSAGE: Final[dict[str, str]] = {"type": "error", "code": "bad_message"}


def _parse_topics(message: dict[str, Any]) -> list[Topic] | None:
    """topics 必須是字串 list 且全部為已知 topic（去重保序）；否則 None。"""
    topics = message.get("topics")
    if not isinstance(topics, list) or not all(isinstance(t, str) for t in topics):
        return None
    unique = list(dict.fromkeys(topics))
    if any(t not in TOPIC_PERMISSIONS for t in unique):
        return None
    return [t for t in unique if t in TOPIC_PERMISSIONS]  # type: ignore[misc]


class _AdminConnection:
    def __init__(self, ws: WebSocket, staff: CurrentStaff, *, clock: Clock) -> None:
        self.ws = ws
        self.staff = staff
        self.clock = clock
        self.topics: set[Topic] = set()
        self.broadcaster = get_broadcaster()

    async def send_subscribed(self) -> None:
        await self.ws.send_json({"type": "subscribed", "topics": sorted(self.topics)})

    async def on_message(self, message: dict[str, Any]) -> None:
        action = message["action"]
        if action not in ("subscribe", "unsubscribe"):
            await self.ws.send_json(_BAD_MESSAGE)
            return
        topics = _parse_topics(message)
        if topics is None:
            await self.ws.send_json(_BAD_MESSAGE)
            return
        if action == "unsubscribe":
            for topic in topics:
                self.topics.discard(topic)
                self.broadcaster.unsubscribe(self.ws, admin_topic_channel(topic))
            await self.send_subscribed()
            return
        denied = [t for t in topics if not self.staff.has(TOPIC_PERMISSIONS[t])]
        allowed = [t for t in topics if t not in denied]
        if denied:
            await self.ws.send_json(
                {"type": "error", "code": "permission_denied", "topics": denied}
            )
        if allowed:
            for topic in allowed:
                self.topics.add(topic)
                self.broadcaster.subscribe(admin_topic_channel(topic), self.ws)
            await self.send_subscribed()

    async def revalidate(self) -> bool:
        try:
            self.staff = await run_in_thread(ws_load_staff, self.ws, clock=self.clock)
        except AppError as exc:
            logger.info("員工 WS 重驗失敗 staff=%s：%s", self.staff.id, exc.code)
            return False
        revoked = sorted(t for t in self.topics if not self.staff.has(TOPIC_PERMISSIONS[t]))
        for topic in revoked:
            self.topics.discard(topic)
            self.broadcaster.unsubscribe(self.ws, admin_topic_channel(topic))
        if revoked:
            await self.ws.send_json(
                {"type": "unsubscribed", "topics": revoked, "reason": "permission_revoked"}
            )
        return True


@router.websocket("/admin")
async def admin_ws(ws: WebSocket, clock: Annotated[Clock, Depends(get_clock)]) -> None:
    try:
        staff = await run_in_thread(ws_load_staff, ws, clock=clock)
    except AppError as exc:
        await ws.close(code=close_code_for(exc))
        return
    await ws.accept()
    conn = _AdminConnection(ws, staff, clock=clock)
    conn.broadcaster.subscribe(staff_channel(staff.id), ws)
    try:
        await ws.send_json({"type": "ready", "topics": []})
        await run_ws_loop(
            ws,
            on_message=conn.on_message,
            revalidate=conn.revalidate,
            interval=REVALIDATE_SECONDS,
        )
    finally:
        conn.broadcaster.unsubscribe(ws)
