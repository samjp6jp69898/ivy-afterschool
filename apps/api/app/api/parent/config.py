"""BACKEND-125：家長端公開設定（不需登入）。

``GET /api/parent/config``：LIFF ID、安親班名稱 / 電話 / Logo 與各項上限；資料來源
``public_config_service``（只含 registry 標記公開的欄位，絕不含 secret）。回應 ``Cache-Control:
public, max-age=60``（security middleware 對此路徑不加 no-store，見 ``PUBLIC_CACHEABLE_PATHS``）。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.services.public_config_service import PublicConfigOut, get_public_config

router = APIRouter(prefix="/config", tags=["parent-config"])

CACHE_CONTROL = "public, max-age=60"


@router.get("", response_model=PublicConfigOut)
def get_config(response: Response, db: Annotated[Session, Depends(get_db)]) -> PublicConfigOut:
    response.headers["Cache-Control"] = CACHE_CONTROL
    return get_public_config(db)
