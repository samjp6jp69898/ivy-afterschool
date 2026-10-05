"""WebSocket router 彙整（BACKEND-020）：各 ws endpoint task 建立 ``router`` 並在此 include。"""

from fastapi import APIRouter

from app.api.ws import admin, parent

ws_router = APIRouter(prefix="/api/ws")
ws_router.include_router(admin.router)
ws_router.include_router(parent.router)

__all__ = ["ws_router"]
