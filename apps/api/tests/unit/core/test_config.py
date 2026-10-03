"""BACKEND-001：env Settings（architecture_decisions §4 的八個 env）。"""

import pytest
from pydantic import ValidationError

from app.core.config import Settings, get_settings

_SECRET = "s" * 48
_ENV_KEYS = (
    "APP_ENV",
    "DATABASE_URL",
    "APP_SECRET_KEY",
    "CORS_ORIGINS",
    "PUBLIC_BASE_URL",
    "SUPABASE_URL",
    "SUPABASE_SERVICE_ROLE_KEY",
    "SENTRY_DSN",
)


@pytest.fixture(autouse=True)
def base_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """清掉開發者 shell 既有的值，只留下一組合法的必填 env。"""
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@127.0.0.1:54342/postgres")
    monkeypatch.setenv("APP_SECRET_KEY", _SECRET)
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://127.0.0.1:5341")
    monkeypatch.setenv("SUPABASE_URL", "http://127.0.0.1:54341")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-role-key-for-test")


def _settings() -> Settings:
    return Settings(_env_file=None)  # type: ignore[call-arg]


def test_config_missing_required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL")

    with pytest.raises(ValidationError) as exc_info:
        _settings()

    assert "database_url" in str(exc_info.value)


@pytest.mark.parametrize("value", ["short", "change-me", "x" * 31])
def test_config_secret_key_too_short(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("APP_SECRET_KEY", value)

    with pytest.raises(ValidationError) as exc_info:
        _settings()

    assert "至少 32 bytes" in str(exc_info.value)


def test_config_secret_key_accepts_32_bytes_and_long_random(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 長度以 UTF-8 bytes 計：11 個中文字 = 33 bytes
    for value in ("x" * 32, "中" * 11, "aB3$" * 12):
        monkeypatch.setenv("APP_SECRET_KEY", value)
        assert _settings().app_secret_key.get_secret_value() == value


def test_config_database_url_normalize(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@127.0.0.1:54342/postgres")
    assert _settings().database_url == "postgresql+psycopg://u:p@127.0.0.1:54342/postgres"

    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@127.0.0.1:54342/postgres")
    assert _settings().database_url == "postgresql+psycopg://u:p@127.0.0.1:54342/postgres"

    for bad in ("mysql://x", "postgresql+asyncpg://u:p@h/db", "postgres://u:p@h/db"):
        monkeypatch.setenv("DATABASE_URL", bad)
        with pytest.raises(ValidationError) as exc_info:
            _settings()
        assert "database_url" in str(exc_info.value)


def test_config_cors_origins_parse(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "http://a.test, http://b.test,http://a.test")
    assert _settings().cors_origins == ["http://a.test", "http://b.test"]

    monkeypatch.setenv("CORS_ORIGINS", "")
    assert _settings().cors_origins == []

    monkeypatch.setenv("CORS_ORIGINS", " , http://a.test ,")
    assert _settings().cors_origins == ["http://a.test"]

    monkeypatch.delenv("CORS_ORIGINS")
    assert _settings().cors_origins == []


def test_config_production_rejects_placeholder_service_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "change-me")
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(ValidationError) as exc_info:
        _settings()
    assert "SUPABASE_SERVICE_ROLE_KEY" in str(exc_info.value)

    monkeypatch.setenv("APP_ENV", "development")
    settings = _settings()
    assert settings.supabase_service_role_key.get_secret_value() == "change-me"
    assert settings.is_production is False

    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "real-service-role-key")
    monkeypatch.setenv("APP_ENV", "production")
    assert _settings().is_production is True


def test_config_app_env_rejects_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "staging")

    with pytest.raises(ValidationError) as exc_info:
        _settings()

    assert "app_env" in str(exc_info.value)


def test_config_repr_hides_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_SECRET_KEY", "x" * 40)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "y" * 40)

    settings = _settings()

    assert "x" * 40 not in repr(settings)
    assert "y" * 40 not in repr(settings)
    assert "x" * 40 not in str(settings.model_dump())
    assert settings.app_secret_key.get_secret_value() == "x" * 40


def test_config_public_base_url_strip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://as.example.com/")
    assert _settings().public_base_url_str == "https://as.example.com"

    monkeypatch.setenv("PUBLIC_BASE_URL", "https://as.example.com/app/")
    assert _settings().public_base_url_str == "https://as.example.com/app"


def test_config_sentry_dsn_empty_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SENTRY_DSN", "")
    assert _settings().sentry_dsn is None

    monkeypatch.setenv("SENTRY_DSN", "https://key@o0.ingest.sentry.io/1")
    assert _settings().sentry_dsn == "https://key@o0.ingest.sentry.io/1"


def test_config_frozen() -> None:
    settings = _settings()

    with pytest.raises(ValidationError):
        settings.app_env = "production"  # type: ignore[misc]


def test_config_get_settings_reads_env_at_call_time(monkeypatch: pytest.MonkeyPatch) -> None:
    get_settings.cache_clear()
    try:
        monkeypatch.setenv("APP_ENV", "test")
        first = get_settings()
        assert first.app_env == "test"
        # lru_cache：同一行程內回傳同一個實例
        assert get_settings() is first

        monkeypatch.setenv("APP_ENV", "production")
        get_settings.cache_clear()
        assert get_settings().app_env == "production"
    finally:
        get_settings.cache_clear()
