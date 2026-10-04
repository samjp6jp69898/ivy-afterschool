"""後台 API 彙整（BACKEND-020）：各 endpoint task 在自己的模組建立 ``router`` 並在此 include。

路由順序：同一 prefix 底下固定路徑（例如 ``/students/import``）必須先於 ``/{student_id}`` 註冊，
由各模組內的宣告順序保證。
"""

from fastapi import APIRouter

from app.api.admin import auth, roles

admin_router = APIRouter(prefix="/api/admin")
admin_router.include_router(auth.router)
admin_router.include_router(roles.router)

__all__ = ["admin_router"]
