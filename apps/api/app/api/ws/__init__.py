"""WebSocket router 彙整（BACKEND-020）：各 ws endpoint task 建立 ``router`` 並在此 include。"""

from fastapi import APIRouter

ws_router = APIRouter(prefix="/api/ws")

__all__ = ["ws_router"]
