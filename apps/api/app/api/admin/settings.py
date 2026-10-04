"""BACKEND-112：後台系統設定 endpoint。

``GET /api/admin/settings``：settings:read；依 registry 順序列出全部設定，secret 欄位遮罩、附
JSON schema（給前端依 registry 渲染表單）。

BACKEND-113 ``PUT /api/admin/settings/{key}``：settings:write；``SettingPutIn`` → BACKEND-110
``put_setting``（422 ``invalid_setting_value`` / 404 ``setting_not_found``）→ commit（after-commit
失效快取，公開設定立即反映）→ ``SettingOut``（secret 遮罩）。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import CurrentStaff, require_permission
from app.core.db import get_db
from app.core.permissions import Permission
from app.core.request_meta import RequestMeta, get_request_meta
from app.schemas.settings import SettingOut, SettingPutIn, SettingsListOut
from app.services import settings_service

router = APIRouter(prefix="/settings", tags=["admin-settings"])


@router.get("", response_model=SettingsListOut)
def list_settings(
    _: Annotated[CurrentStaff, Depends(require_permission(Permission.SETTINGS_READ))],
    db: Annotated[Session, Depends(get_db)],
) -> SettingsListOut:
    return SettingsListOut(items=settings_service.list_settings_for_admin(db))


@router.put("/{key}", response_model=SettingOut)
def put_setting(
    key: str,
    body: SettingPutIn,
    staff: Annotated[CurrentStaff, Depends(require_permission(Permission.SETTINGS_WRITE))],
    db: Annotated[Session, Depends(get_db)],
    meta: Annotated[RequestMeta, Depends(get_request_meta)],
) -> SettingOut:
    out = settings_service.put_setting(db, key, body.value, actor=staff, meta=meta)
    db.commit()
    return out
