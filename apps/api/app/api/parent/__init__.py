"""家長端 API 彙整（BACKEND-020）：各 endpoint task 在自己的模組建立 ``router`` 並在此 include。"""

from fastapi import APIRouter

parent_router = APIRouter(prefix="/api/parent")

__all__ = ["parent_router"]
