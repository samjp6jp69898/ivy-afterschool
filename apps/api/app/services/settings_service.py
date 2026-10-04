"""BACKEND-108：system_settings 的型別化讀取（architecture_decisions §4）。

營運模組讀設定的唯一入口：``get_setting(db, PICKUP_WINDOW).auto_expire_minutes``。

- TTL in-process cache（``time.monotonic()`` 判斷過期、``threading.Lock`` 保護）；後台修改後由
  寫入路徑呼叫 ``invalidate_setting`` 立即失效。
- 列不存在 / value 不通過 schema → 記 log 後回 registry default，一筆壞資料不讓功能全掛。
- secret 欄位在 DB 存 ``encrypt_token`` 字串，讀取時解密成明文放進回傳的 model（只給後端內部
  使用）；解密失敗該欄位視為 None。log 只記 key 與欄位名，不記值。
- 回傳的 model 為 frozen 實例（registry 的 schema 皆 frozen），呼叫端不可修改。
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
from app.core.settings_registry import SettingKey
from app.models.reference import SystemSetting

logger = logging.getLogger(__name__)

SETTINGS_CACHE_TTL_SECONDS: Final = 60

_cache: dict[str, tuple[BaseModel, float]] = {}
_lock = threading.Lock()
# invalidate / clear 時遞增：查詢期間被失效的結果不回寫快取，避免舊值蓋過失效
_generation = 0


def get_setting[M: BaseModel](session: Session, key: SettingKey[M]) -> M:
    now = time.monotonic()
    with _lock:
        cached = _cache.get(key.key)
        if cached is not None and cached[1] > now:
            return cached[0]  # type: ignore[return-value]
        generation = _generation

    value = _load(session, key)

    with _lock:
        if generation == _generation:
            _cache[key.key] = (value, time.monotonic() + SETTINGS_CACHE_TTL_SECONDS)
    return value


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
    definition = key.definition()
    raw = session.execute(
        select(SystemSetting.value).where(SystemSetting.key == key.key)
    ).scalar_one_or_none()
    if raw is None:
        logger.warning("system_settings 缺少 %s，改用預設值", key.key)
        return definition.default

    value = _decrypt_secret_fields(key.key, raw, definition.secret_fields)
    try:
        return key.schema.model_validate(value)
    except ValidationError as exc:
        # 只記錯誤位置，不記值（可能含 secret 明文）
        locations = sorted(
            {".".join(str(p) for p in err["loc"]) or "<root>" for err in exc.errors()}
        )
        logger.error("system_settings %s 不通過 schema 驗證（%s），改用預設值", key.key, locations)
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
