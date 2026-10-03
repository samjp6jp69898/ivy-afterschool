"""BACKEND-001：env Settings（architecture_decisions §4）。

env 只放基礎設施與 secret，執行期恰為下列十項；營運參數一律走 DB ``system_settings``
（``app/core/settings_registry.py``），不得在此新增業務設定。JWT TTL、cookie 名稱、
節流門檻等屬於各模組的程式常數。

``MIGRATION_DATABASE_URL``（owner 連線）刻意不是欄位：執行期程式碼不讀 owner 連線，只給
BACKEND-535 的 migrate 指令；env 中有它時因 ``extra="ignore"`` 被忽略。

模組 import 時不讀 env：只有呼叫 ``get_settings()`` 才建立實例。
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Annotated, Any, Final, Literal

from pydantic import (
    AnyHttpUrl,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

_PLACEHOLDER: Final = "change-me"
_MIN_SECRET_BYTES: Final = 32
_DB_SCHEME: Final = "postgresql+psycopg://"
_PLAIN_DB_SCHEME: Final = "postgresql://"
_R2_BUCKET = re.compile(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]")
# 本機 SeaweedFS 固定開發值（apps/api/.env.example、architecture_decisions §10），正式環境不得沿用
_LOCAL_R2_SECRET: Final = "afterschool-local-secret"  # noqa: S105


def _without_inputs(exc: ValidationError) -> ValidationError:
    """保留 type / loc / ctx（錯誤訊息由此重建），input 一律換成 ***。"""
    details: list[Any] = []
    for err in exc.errors():
        detail: dict[str, Any] = {"type": err["type"], "loc": err["loc"], "input": "***"}
        if "ctx" in err:
            detail["ctx"] = err["ctx"]
        details.append(detail)
    return ValidationError.from_exception_data(exc.title, details, hide_input=True)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
        hide_input_in_errors=True,
    )

    def __init__(self, **values: Any) -> None:
        # hide_input_in_errors 只影響 str(e)；e.errors() 仍帶 input，
        # model 層錯誤的 input 是整包設定值（含 DATABASE_URL 密碼與 APP_SECRET_KEY）。
        # 重建成 input='***' 的錯誤，並在 except 外 raise，讓原例外不掛在 __context__ 上。
        sanitized: ValidationError | None = None
        try:
            super().__init__(**values)
        except ValidationError as exc:
            sanitized = _without_inputs(exc)
        if sanitized is not None:
            raise sanitized

    app_env: Literal["development", "test", "production"] = "development"
    # 含 app_backend 密碼：維持 str（建 engine 的呼叫端不變），但不進 repr / str
    database_url: str = Field(repr=False)
    app_secret_key: SecretStr
    # 逗號分隔字串；NoDecode 關掉 pydantic-settings 對 list 的 JSON 解碼，交給 validator 切
    cors_origins: Annotated[list[str], NoDecode] = []
    public_base_url: AnyHttpUrl
    # S3 相容 endpoint（本機 SeaweedFS / 雲端 Cloudflare R2）
    r2_endpoint_url: AnyHttpUrl
    r2_access_key_id: str
    r2_secret_access_key: SecretStr
    r2_bucket: str
    # DSN 內含 key，同樣不進 repr / str
    sentry_dsn: str | None = Field(default=None, repr=False)

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

    @field_validator("r2_bucket")
    @classmethod
    def _check_r2_bucket(cls, value: str) -> str:
        if not _R2_BUCKET.fullmatch(value):
            raise ValueError("R2_BUCKET 必須符合 S3 bucket 命名（3~63 字元小寫英數、. 與 -）")
        return value

    @field_validator("sentry_dsn", mode="before")
    @classmethod
    def _empty_dsn_is_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def _check_production_r2(self) -> Settings:
        if not self.is_production:
            return self
        if self.r2_endpoint_url.scheme != "https":
            raise ValueError("正式環境的 R2_ENDPOINT_URL 必須是 https")
        if self.r2_secret_access_key.get_secret_value() in (_PLACEHOLDER, _LOCAL_R2_SECRET):
            raise ValueError("正式環境的 R2_SECRET_ACCESS_KEY 不可為 change-me 或本機開發值")
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
