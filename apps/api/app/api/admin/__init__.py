"""後台 API 彙整（BACKEND-020）：各 endpoint task 在自己的模組建立 ``router`` 並在此 include。"""

from fastapi import APIRouter

admin_router = APIRouter(prefix="/api/admin")

__all__ = ["admin_router"]
