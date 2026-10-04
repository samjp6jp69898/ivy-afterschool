"""健康檢查 router（BACKEND-020 建立彙整檔；BACKEND-021 實作 ``GET /api/health``）。

Railway healthcheck（INFRA-032）與部署後 smoke（INFRA-039）使用；不需登入、不受權限守衛、不寫
access log（BACKEND-004）。``app_name`` 為程式常數，健康檢查不可依賴業務資料；DB 失敗時只回固定
文案，不帶例外訊息或連線字串。
"""

from __future__ import annotations

import logging
from typing import Annotated, Final, Literal

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.db import get_db

logger = logging.getLogger(__name__)

APP_NAME: Final = "afterschool-api"
_DB_TIMEOUT_MS: Final = 2000

router = APIRouter(prefix="/api", tags=["health"])


class HealthOut(BaseModel):
    status: Literal["ok", "degraded"]
    app_name: str
    db: Literal["ok", "error"]


@router.get("/health", response_model=HealthOut)
def health(db: Annotated[Session, Depends(get_db)]) -> HealthOut | JSONResponse:
    try:
        db.execute(text(f"set local statement_timeout = {_DB_TIMEOUT_MS}"))
        db.execute(text("select 1"))
    except SQLAlchemyError as exc:
        # 只記例外類別，不記訊息（可能含連線字串）
        logger.warning("health check DB 失敗：%s", type(exc).__name__)
        db.rollback()
        degraded = HealthOut(status="degraded", app_name=APP_NAME, db="error")
        return JSONResponse(status_code=503, content=degraded.model_dump())
    return HealthOut(status="ok", app_name=APP_NAME, db="ok")
