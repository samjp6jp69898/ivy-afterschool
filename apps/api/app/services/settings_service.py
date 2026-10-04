"""BACKEND-108：system_settings 的型別化讀取（architecture_decisions §4）。

營運模組讀設定的唯一入口：``get_setting(db, PICKUP_WINDOW).auto_expire_minutes``。

- TTL in-process cache（``time.monotonic()`` 判斷過期、``threading.Lock`` 保護）；後台修改後由
  寫入路徑呼叫 ``invalidate_setting`` 立即失效。
- 列不存在 / value 不通過 schema → 記 log 後回 registry default，一筆壞資料不讓功能全掛。
- secret 欄位在 DB 存 ``encrypt_token`` 字串，讀取時解密成明文放進回傳的 model（只給後端內部
  使用）；解密失敗該欄位視為 None。log 只記 key 與欄位名，不記值。
- 回傳的 model 為 frozen 實例（registry 的 schema 皆 frozen），且一律是 deep copy：RootModel
  （NotificationToggles）的內層 dict 擋不住就地修改，回傳副本讓呼叫端改不到快取與 registry 預設值。

BACKEND-110：``put_setting``（移植 ivy ``api/system_config.py::upsert_config`` 的 upsert 流程；去掉
finance 權限分支）。

- key 未註冊 → 404 ``setting_not_found``。
- secret 欄位：送來的值以 ``****`` 開頭或等於 ``********``（前端回傳的遮罩值）→ 沿用 DB 現有密文
  （以明文狀態參與驗證）；``null`` → 清除；其他字串 → 新明文。
- 以 registry schema 驗證（明文狀態）→ 422 ``invalid_setting_value``，details 為 Pydantic 錯誤清單
  （只留 loc / msg / type，不回顯輸入）。寫入的是驗證後 ``model_dump(mode="json")``（不寫原始
  value，NaN / 孤立 surrogate 等壞值不會進 jsonb），secret 欄位再以 ``encrypt_token`` 加密。
- upsert 列（``is_secret`` 依 registry、``updated_by = actor.id``）、稽核 ``settings.update``
  （entity_type ``system_setting``、entity_id = key，before / after 為遮罩後的值）、
  ``run_after_commit`` 失效快取（commit 前不呼叫 invalidate，也不以同一 session 呼叫
  ``get_setting``，避免未 commit 的值進快取）。只 flush 不 commit。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING, Any, Final

from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.crypto import DecryptionError, decrypt_token, encrypt_token
from app.core.errors import AppError, NotFoundError
from app.core.settings_registry import REGISTRY, SettingDef, SettingKey
from app.core.tx_hooks import run_after_commit
from app.models.account import StaffUser
from app.models.reference import SystemSetting
from app.schemas.settings import SettingOut
from app.services import audit_service

if TYPE_CHECKING:
    from app.api.deps import CurrentStaff
    from app.core.request_meta import RequestMeta

logger = logging.getLogger(__name__)

SETTINGS_CACHE_TTL_SECONDS: Final = 60
_MASK_FULL_MAX_LEN: Final = 8
_MASK_PREFIX: Final = "****"
_MASK_FULL: Final = "*" * _MASK_FULL_MAX_LEN

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


def _masked_dump(definition: SettingDef[Any], model: BaseModel) -> dict[str, Any]:
    value = model.model_dump(mode="json")
    for field in definition.secret_fields:
        if field in value:
            value[field] = mask_secret(value[field])
    return value


def _is_masked_value(value: Any) -> bool:
    return isinstance(value, str) and (value.startswith(_MASK_PREFIX) or value == _MASK_FULL)


def _merge_secret_fields(
    definition: SettingDef[Any], value: dict[str, Any], current: BaseModel
) -> tuple[dict[str, Any], frozenset[str]]:
    """前端回傳的遮罩值代表「不修改」：以 DB 現有明文參與驗證（解密失敗視為未設定），
    並回傳要沿用原密文的欄位集合。"""
    merged = dict(value)
    kept: set[str] = set()
    for field in definition.secret_fields:
        if _is_masked_value(merged.get(field)):
            merged[field] = getattr(current, field, None)
            kept.add(field)
    return merged, frozenset(kept)


def _validate(definition: SettingDef[Any], value: dict[str, Any]) -> BaseModel:
    try:
        model: BaseModel = definition.schema.model_validate(value)
    except ValidationError as exc:
        details = [
            {
                "loc": list(err.get("loc", ())),
                "msg": err.get("msg", ""),
                "type": err.get("type", ""),
            }
            for err in exc.errors()
        ]
        raise AppError(
            "invalid_setting_value", "設定值不符合格式", status=422, details=details
        ) from None
    return model


def _encrypt_secret_fields(
    definition: SettingDef[Any],
    stored: dict[str, Any],
    *,
    kept: frozenset[str],
    raw: dict[str, Any] | None,
) -> None:
    """新明文加密存入；遮罩保留的欄位沿用 DB 現有密文（不重新加密）。"""
    for field in definition.secret_fields:
        if field in kept:
            existing = raw.get(field) if raw is not None else None
            stored[field] = existing if isinstance(existing, str) else None
            continue
        plain = stored.get(field)
        if plain is not None:
            stored[field] = encrypt_token(str(plain))


def put_setting(
    session: Session,
    key: str,
    value: dict[str, Any],
    *,
    actor: CurrentStaff,
    meta: RequestMeta,
) -> SettingOut:
    definition = REGISTRY.get(key)
    if definition is None:
        raise NotFoundError("setting_not_found", "找不到此設定")

    row = session.execute(
        select(SystemSetting).where(SystemSetting.key == key).with_for_update()
    ).scalar_one_or_none()
    # 直接讀列，不走 get_setting（避免把交易內的值寫進快取）
    raw = row.value if row is not None else None
    current = _model_from_raw(key, definition, raw)

    merged, kept = _merge_secret_fields(definition, value, current)
    model = _validate(definition, merged)
    stored = model.model_dump(mode="json")
    _encrypt_secret_fields(definition, stored, kept=kept, raw=raw)

    if row is None:
        row = SystemSetting(
            key=key, value=stored, is_secret=definition.is_secret, updated_by=actor.id
        )
        session.add(row)
    else:
        row.value = stored
        row.is_secret = definition.is_secret
        row.updated_by = actor.id
    session.flush()
    # updated_at 由 DB trigger 設定，重新讀回
    session.refresh(row, attribute_names=["updated_at"])

    audit_service.record(
        session,
        actor=audit_service.Actor.staff(actor),
        action="settings.update",
        entity_type="system_setting",
        entity_id=key,
        before=_masked_dump(definition, current),
        after=_masked_dump(definition, model),
        meta=meta,
    )
    run_after_commit(session, lambda: invalidate_setting(key))

    return SettingOut(
        key=key,
        group=definition.group,
        label=definition.label,
        is_secret=definition.is_secret,
        value=_masked_dump(definition, model),
        json_schema=definition.schema.model_json_schema(),
        updated_at=row.updated_at,
        updated_by_name=actor.display_name,
    )
