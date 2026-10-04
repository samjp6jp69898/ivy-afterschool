"""健康檢查 router（BACKEND-020 建立彙整檔；``GET /api/health`` 由 BACKEND-021 實作）。"""

from fastapi import APIRouter

router = APIRouter(prefix="/api", tags=["health"])
