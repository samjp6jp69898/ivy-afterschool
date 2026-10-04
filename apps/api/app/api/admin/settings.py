"""BACKEND-112：後台系統設定 endpoint。

``GET /api/admin/settings``：settings:read；依 registry 順序列出全部設定，secret 欄位遮罩、附
JSON schema（給前端依 registry 渲染表單）。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import CurrentStaff, require_permission
from app.core.db import get_db
from app.core.permissions import Permission
from app.schemas.settings import SettingsListOut
from app.services import settings_service

router = APIRouter(prefix="/settings", tags=["admin-settings"])


@router.get("", response_model=SettingsListOut)
def list_settings(
    _: Annotated[CurrentStaff, Depends(require_permission(Permission.SETTINGS_READ))],
    db: Annotated[Session, Depends(get_db)],
) -> SettingsListOut:
    return SettingsListOut(items=settings_service.list_settings_for_admin(db))
