"""BACKEND-108：system_settings 的型別化讀取（architecture_decisions §4）。

營運模組讀設定的唯一入口：``get_setting(db, PICKUP_WINDOW).auto_expire_minutes``。

- TTL in-process cache（``time.monotonic()`` 判斷過期、``threading.Lock`` 保護）；後台修改後由
  寫入路徑呼叫 ``invalidate_setting`` 立即失效。
- 列不存在 / value 不通過 schema → 記 log 後回 registry default，一筆壞資料不讓功能全掛。
- secret 欄位在 DB 存 ``encrypt_token`` 字串，讀取時解密成明文放進回傳的 model（只給後端內部
  使用）；解密失敗該欄位視為 None。log 只記 key 與欄位名，不記值。
- 回傳的 model 為 frozen 實例（registry 的 schema 皆 frozen），且一律是 deep copy：RootModel
  （NotificationToggles）的內層 dict 擋不住就地修改，回傳副本讓呼叫端改不到快取與 registry 預設值。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Final

from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.crypto import DecryptionError, decrypt_token
from app.core.settings_registry import REGISTRY, SettingDef, SettingKey
from app.models.account import StaffUser
from app.models.reference import SystemSetting
from app.schemas.settings import SettingOut

logger = logging.getLogger(__name__)

SETTINGS_CACHE_TTL_SECONDS: Final = 60
_MASK_FULL_MAX_LEN: Final = 8

_cache: dict[str, tuple[BaseModel, float]] = {}
_lock = threading.Lock()
# invalidate / clear 時遞增：查詢期間被失效的結果不回寫快取，避免舊值蓋過失效
_generation = 0


def get_setting[M: BaseModel](session: Session, key: SettingKey[M]) -> M:
    now = time.monotonic()
    with _lock:
        cached = _cache.get(key.key)
        if cached is not None and cached[1] > now:
            return cached[0].model_copy(deep=True)  # type: ignore[return-value]
        generation = _generation

    value = _load(session, key)

    with _lock:
        if generation == _generation:
            _cache[key.key] = (value, time.monotonic() + SETTINGS_CACHE_TTL_SECONDS)
    return value.model_copy(deep=True)


def invalidate_setting(key: str) -> None:
    global _generation
    with _lock:
        _cache.pop(key, None)
        _generation += 1


def clear_settings_cache() -> None:
    """測試用：清空全部快取。"""
    global _generation
    with _lock:
        _cache.clear()
        _generation += 1


def _load[M: BaseModel](session: Session, key: SettingKey[M]) -> M:
    raw = session.execute(
        select(SystemSetting.value).where(SystemSetting.key == key.key)
    ).scalar_one_or_none()
    return _model_from_raw(key.key, key.definition(), raw)


def _model_from_raw[M: BaseModel](
    key: str, definition: SettingDef[M], raw: dict[str, Any] | None
) -> M:
    """列不存在或不通過 schema 驗證 → registry default；secret 欄位解密成明文。"""
    if raw is None:
        logger.warning("system_settings 缺少 %s，改用預設值", key)
        return definition.default

    value = _decrypt_secret_fields(key, raw, definition.secret_fields)
    try:
        return definition.schema.model_validate(value)
    except ValidationError as exc:
        # 只記錯誤位置，不記值（可能含 secret 明文）
        locations = sorted(
            {".".join(str(p) for p in err["loc"]) or "<root>" for err in exc.errors()}
        )
        logger.error("system_settings %s 不通過 schema 驗證（%s），改用預設值", key, locations)
        return definition.default


def _decrypt_secret_fields(
    key: str, raw: dict[str, Any], secret_fields: tuple[str, ...]
) -> dict[str, Any]:
    if not secret_fields:
        return raw
    value = dict(raw)
    for field in secret_fields:
        token = value.get(field)
        if not isinstance(token, str):
            continue
        try:
            value[field] = decrypt_token(token)
        except DecryptionError:
            logger.error("system_settings %s 的 %s 解密失敗，視為未設定", key, field)
            value[field] = None
    return value


def mask_secret(value: str | None) -> str | None:
    """None → None；長度 ≤ 8 → 全遮；否則只露末 4 碼。"""
    if value is None:
        return None
    if len(value) <= _MASK_FULL_MAX_LEN:
        return "*" * _MASK_FULL_MAX_LEN
    return "****" + value[-4:]


def list_settings_for_admin(session: Session) -> list[SettingOut]:
    """後台設定頁：依 registry 順序列出全部 key。直接查 DB（不走 cache）；secret 欄位遮罩。"""
    rows = {
        row.SystemSetting.key: row
        for row in session.execute(
            select(SystemSetting, StaffUser.display_name).outerjoin(
                StaffUser, StaffUser.id == SystemSetting.updated_by
            )
        )
    }
    items: list[SettingOut] = []
    for key, definition in REGISTRY.items():
        row = rows.get(key)
        model = _model_from_raw(key, definition, row.SystemSetting.value if row else None)
        value = model.model_dump(mode="json")
        for field in definition.secret_fields:
            if field in value:
                value[field] = mask_secret(value[field])
        items.append(
            SettingOut(
                key=key,
                group=definition.group,
                label=definition.label,
                is_secret=definition.is_secret,
                value=value,
                json_schema=definition.schema.model_json_schema(),
                updated_at=row.SystemSetting.updated_at if row else None,
                updated_by_name=row.display_name if row else None,
            )
        )
    return items
