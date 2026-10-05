"""BACKEND-227：家長 WebSocket ``/api/ws/parent``（只收自己小孩的 pickup / homework / attendance
與個人通知；domain_spec §2）。

移植 ivy ``api/inbox_ws.py::employee_inbox_ws`` 的「握手驗證 → 固定訂閱個人頻道 → 定期重驗」；
頻道改為 ``parent_channel`` + 每個小孩的 ``student_channel``，家長端不能自選頻道。

1. ``ws_load_parent`` 失敗 → close 4401（外站 Origin 由 BACKEND-019 以 4403 拒絕）。
2. accept；訂閱 ``parent_channel(parent.id)`` 與每個 ``student_channel(sid)``（sid ∈ BACKEND-179
   ``get_parent_student_ids``，已排除封存的監護關係 / 學生）；回
   ``{"type":"ready","children":[...student ids]}``。
3. 任何 subscribe / unsubscribe（或其他非 ping 的 action）→
   ``{"type":"error","code":"not_allowed"}``。
4. 每 ``REVALIDATE_SECONDS`` 秒重驗：token / 帳號狀態失效 → 4401；重新計算小孩清單，解除綁定或封存
   的學生取消其 student channel、新綁定的學生新增訂閱，有變動即回
   ``{"type":"children_changed","children":[...]}``。
5. 斷線時取消全部訂閱。訊息內容由 BACKEND-224 的呼叫端裁切（家長版），本 endpoint 原樣轉送。
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, Final
from uuid import UUID

from fastapi import APIRouter, Depends, WebSocket

from app.api.deps import CurrentParent
from app.api.ws import common as ws_common
from app.api.ws.common import close_code_for, run_in_thread, run_ws_loop, ws_load_parent
from app.core.clock import Clock, get_clock
from app.core.errors import AppError
from app.realtime.broadcaster import get_broadcaster
from app.realtime.publish import parent_channel, student_channel
from app.services.parent_scope import get_parent_student_ids

logger = logging.getLogger(__name__)

router = APIRouter()

# 測試以 monkeypatch 縮短；執行期才讀取模組屬性
REVALIDATE_SECONDS: float = ws_common.REVALIDATE_SECONDS

_NOT_ALLOWED: Final[dict[str, str]] = {"type": "error", "code": "not_allowed"}


def _load_children(parent_id: UUID) -> list[UUID]:
    with ws_common.open_session() as db:
        return get_parent_student_ids(db, parent_id)


class _ParentConnection:
    def __init__(
        self, ws: WebSocket, parent: CurrentParent, children: list[UUID], *, clock: Clock
    ) -> None:
        self.ws = ws
        self.parent = parent
        self.clock = clock
        self.children = children
        self.broadcaster = get_broadcaster()

    def subscribe_all(self) -> None:
        self.broadcaster.subscribe(parent_channel(self.parent.id), self.ws)
        for student_id in self.children:
            self.broadcaster.subscribe(student_channel(student_id), self.ws)

    async def on_message(self, message: dict[str, Any]) -> None:
        # 家長端不能自選頻道；ping 已由 run_ws_loop 處理
        await self.ws.send_json(_NOT_ALLOWED)

    async def revalidate(self) -> bool:
        try:
            self.parent = await run_in_thread(ws_load_parent, self.ws, clock=self.clock)
            current = await run_in_thread(_load_children, self.parent.id)
        except AppError as exc:
            logger.info("家長 WS 重驗失敗 parent=%s：%s", self.parent.id, exc.code)
            return False
        if current == self.children:
            return True
        for removed in set(self.children) - set(current):
            self.broadcaster.unsubscribe(self.ws, student_channel(removed))
        for added in set(current) - set(self.children):
            self.broadcaster.subscribe(student_channel(added), self.ws)
        self.children = current
        await self.ws.send_json({"type": "children_changed", "children": [str(s) for s in current]})
        return True


@router.websocket("/parent")
async def parent_ws(ws: WebSocket, clock: Annotated[Clock, Depends(get_clock)]) -> None:
    try:
        parent = await run_in_thread(ws_load_parent, ws, clock=clock)
        children = await run_in_thread(_load_children, parent.id)
    except AppError as exc:
        await ws.close(code=close_code_for(exc))
        return
    await ws.accept()
    conn = _ParentConnection(ws, parent, children, clock=clock)
    conn.subscribe_all()
    try:
        await ws.send_json({"type": "ready", "children": [str(s) for s in children]})
        await run_ws_loop(
            ws,
            on_message=conn.on_message,
            revalidate=conn.revalidate,
            interval=REVALIDATE_SECONDS,
        )
    finally:
        conn.broadcaster.unsubscribe(ws)
