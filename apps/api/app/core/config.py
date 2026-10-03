"""BACKEND-001：env Settings（architecture_decisions §4）。

env 只放基礎設施與 secret，恰為下列八項；營運參數一律走 DB ``system_settings``
（``app/core/settings_registry.py``），不得在此新增業務設定。JWT TTL、cookie 名稱、
節流門檻等屬於各模組的程式常數。

模組 import 時不讀 env：只有呼叫 ``get_settings()`` 才建立實例。
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Final, Literal

from pydantic import AnyHttpUrl, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

_PLACEHOLDER: Final = "change-me"
_MIN_SECRET_BYTES: Final = 32
_DB_SCHEME: Final = "postgresql+psycopg://"
_PLAIN_DB_SCHEME: Final = "postgresql://"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", frozen=True
    )

    app_env: Literal["development", "test", "production"] = "development"
    database_url: str
    app_secret_key: SecretStr
    # 逗號分隔字串；NoDecode 關掉 pydantic-settings 對 list 的 JSON 解碼，交給 validator 切
    cors_origins: Annotated[list[str], NoDecode] = []
    public_base_url: AnyHttpUrl
    supabase_url: AnyHttpUrl
    supabase_service_role_key: SecretStr
    sentry_dsn: str | None = None

    @field_validator("app_secret_key")
    @classmethod
    def _check_secret_key(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        if raw == _PLACEHOLDER or len(raw.encode("utf-8")) < _MIN_SECRET_BYTES:
            raise ValueError("APP_SECRET_KEY 至少 32 bytes 且不可為 change-me")
        return value

    @field_validator("database_url")
    @classmethod
    def _normalize_database_url(cls, value: str) -> str:
        if value.startswith(_DB_SCHEME):
            return value
        if value.startswith(_PLAIN_DB_SCHEME):
            return _DB_SCHEME + value.removeprefix(_PLAIN_DB_SCHEME)
        raise ValueError("DATABASE_URL 必須是 postgresql+psycopg:// 或 postgresql:// 開頭")

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_cors_origins(cls, value: object) -> object:
        items = value.split(",") if isinstance(value, str) else value
        if not isinstance(items, list):
            return items
        # 去空白、去空項、去重保序
        return list(dict.fromkeys(s for item in items if (s := str(item).strip())))

    @field_validator("sentry_dsn", mode="before")
    @classmethod
    def _empty_dsn_is_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def _check_production_secrets(self) -> Settings:
        if self.is_production and self.supabase_service_role_key.get_secret_value() == _PLACEHOLDER:
            raise ValueError("正式環境的 SUPABASE_SERVICE_ROLE_KEY 不可為 change-me")
        return self

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def public_base_url_str(self) -> str:
        """無結尾斜線的對外網址，供通知連結組字串。"""
        return str(self.public_base_url).rstrip("/")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """FastAPI dependency；測試以 ``get_settings.cache_clear()`` 或 dependency_overrides 換掉。"""
    return Settings()
