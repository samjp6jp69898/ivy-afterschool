"""BACKEND-001：env Settings（architecture_decisions §4 的執行期 env）。"""

import pytest
from pydantic import ValidationError

from app.core.config import Settings, get_settings

_SECRET = "s" * 48
_LOCAL_R2_KEY = "afterschool-local-secret"  # 本機 SeaweedFS 固定開發值（apps/api/.env.example）
_CLOUD_R2_SECRET = "k" * 40
_CLOUD_R2_ENDPOINT = "https://acct.r2.cloudflarestorage.com"
_ENV_KEYS = (
    "APP_ENV",
    "DATABASE_URL",
    "MIGRATION_DATABASE_URL",
    "APP_SECRET_KEY",
    "CORS_ORIGINS",
    "PUBLIC_BASE_URL",
    "R2_ENDPOINT_URL",
    "R2_ACCESS_KEY_ID",
    "R2_SECRET_ACCESS_KEY",
    "R2_BUCKET",
    "SENTRY_DSN",
)


@pytest.fixture(autouse=True)
def base_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """清掉開發者 shell 既有的值，只留下一組合法的必填 env（本機 SeaweedFS 值）。"""
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@127.0.0.1:54342/postgres")
    monkeypatch.setenv("APP_SECRET_KEY", _SECRET)
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://127.0.0.1:5341")
    monkeypatch.setenv("R2_ENDPOINT_URL", "http://127.0.0.1:54344")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "afterschool")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", _LOCAL_R2_KEY)
    monkeypatch.setenv("R2_BUCKET", "afterschool-local")


def _settings() -> Settings:
    return Settings(_env_file=None)


def _use_cloud_r2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("R2_ENDPOINT_URL", _CLOUD_R2_ENDPOINT)
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", _CLOUD_R2_SECRET)


def test_config_missing_required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL")

    with pytest.raises(ValidationError) as exc_info:
        _settings()

    assert "database_url" in str(exc_info.value)


@pytest.mark.parametrize(
    "key",
    [
        "APP_SECRET_KEY",
        "PUBLIC_BASE_URL",
        "R2_ENDPOINT_URL",
        "R2_ACCESS_KEY_ID",
        "R2_SECRET_ACCESS_KEY",
        "R2_BUCKET",
    ],
)
def test_config_missing_each_required_env(monkeypatch: pytest.MonkeyPatch, key: str) -> None:
    monkeypatch.delenv(key)

    with pytest.raises(ValidationError) as exc_info:
        _settings()

    assert key.lower() in str(exc_info.value)


def test_config_fields_exact() -> None:
    assert set(Settings.model_fields) == {
        "app_env",
        "database_url",
        "app_secret_key",
        "cors_origins",
        "public_base_url",
        "r2_endpoint_url",
        "r2_access_key_id",
        "r2_secret_access_key",
        "r2_bucket",
        "sentry_dsn",
    }


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


def test_config_local_r2_values(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings()

    assert str(settings.r2_endpoint_url).rstrip("/") == "http://127.0.0.1:54344"
    assert settings.r2_access_key_id == "afterschool"
    assert settings.r2_secret_access_key.get_secret_value() == _LOCAL_R2_KEY
    assert settings.r2_bucket == "afterschool-local"

    monkeypatch.setenv("R2_ENDPOINT_URL", "not-a-url")
    with pytest.raises(ValidationError) as exc_info:
        _settings()
    assert "r2_endpoint_url" in str(exc_info.value)


def test_config_r2_bucket_format(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("R2_BUCKET", "Afterschool_Bucket")
    with pytest.raises(ValidationError) as exc_info:
        _settings()
    assert "r2_bucket" in str(exc_info.value)

    monkeypatch.setenv("R2_BUCKET", "afterschool-local")
    assert _settings().r2_bucket == "afterschool-local"


@pytest.mark.parametrize("bucket", ["abc", "a.b-c", "0" * 63, "afterschool-prod"])
def test_config_r2_bucket_accepts(monkeypatch: pytest.MonkeyPatch, bucket: str) -> None:
    monkeypatch.setenv("R2_BUCKET", bucket)

    assert _settings().r2_bucket == bucket


@pytest.mark.parametrize(
    "bucket",
    ["ab", "a" * 64, "-abc", "abc-", ".abc", "abc.", "ab_c", "ABC", "ab c", "afterschool\n", ""],
)
def test_config_r2_bucket_rejects(monkeypatch: pytest.MonkeyPatch, bucket: str) -> None:
    monkeypatch.setenv("R2_BUCKET", bucket)

    with pytest.raises(ValidationError) as exc_info:
        _settings()

    assert "r2_bucket" in str(exc_info.value)


def test_config_production_r2_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    _use_cloud_r2(monkeypatch)

    for secret in ("change-me", _LOCAL_R2_KEY):
        monkeypatch.setenv("R2_SECRET_ACCESS_KEY", secret)
        with pytest.raises(ValidationError) as exc_info:
            _settings()
        assert "R2_SECRET_ACCESS_KEY" in str(exc_info.value)

    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", _CLOUD_R2_SECRET)
    monkeypatch.setenv("R2_ENDPOINT_URL", "http://127.0.0.1:54344")
    with pytest.raises(ValidationError) as exc_info:
        _settings()
    assert "R2_ENDPOINT_URL" in str(exc_info.value)

    monkeypatch.setenv("R2_ENDPOINT_URL", _CLOUD_R2_ENDPOINT)
    settings = _settings()
    assert settings.is_production is True
    assert settings.r2_secret_access_key.get_secret_value() == _CLOUD_R2_SECRET


@pytest.mark.parametrize("env", ["development", "test"])
def test_config_non_production_allows_local_r2(monkeypatch: pytest.MonkeyPatch, env: str) -> None:
    monkeypatch.setenv("APP_ENV", env)

    settings = _settings()
    assert settings.is_production is False
    assert settings.r2_secret_access_key.get_secret_value() == _LOCAL_R2_KEY

    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "change-me")
    assert _settings().r2_secret_access_key.get_secret_value() == "change-me"


def test_config_app_env_rejects_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "staging")

    with pytest.raises(ValidationError) as exc_info:
        _settings()

    assert "app_env" in str(exc_info.value)


def test_config_repr_hides_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_SECRET_KEY", "x" * 40)
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "y" * 40)

    settings = _settings()

    assert "x" * 40 not in repr(settings)
    assert "y" * 40 not in repr(settings)
    assert "x" * 40 not in str(settings.model_dump())
    assert "y" * 40 not in str(settings.model_dump())
    assert settings.app_secret_key.get_secret_value() == "x" * 40
    assert settings.r2_secret_access_key.get_secret_value() == "y" * 40


def test_config_repr_hides_database_url_password(monkeypatch: pytest.MonkeyPatch) -> None:
    url = "postgresql+psycopg://app_backend:Pw123@127.0.0.1:54342/postgres"
    monkeypatch.setenv("DATABASE_URL", url)

    settings = _settings()

    assert "Pw123" not in repr(settings)
    assert "Pw123" not in str(settings)
    assert settings.database_url == url


def test_config_repr_hides_sentry_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    dsn = "https://abcdef123456@o1.ingest.sentry.io/9"
    monkeypatch.setenv("SENTRY_DSN", dsn)

    settings = _settings()

    assert "abcdef123456" not in repr(settings)
    assert "abcdef123456" not in str(settings)
    assert settings.sentry_dsn == dsn


def test_config_ignores_migration_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "MIGRATION_DATABASE_URL", "postgresql://postgres:pw@127.0.0.1:54342/postgres"
    )

    settings = _settings()

    assert "migration_database_url" not in Settings.model_fields
    assert not hasattr(settings, "migration_database_url")
    assert "pw" not in repr(settings)
    assert settings.database_url == "postgresql+psycopg://u:p@127.0.0.1:54342/postgres"


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
        _use_cloud_r2(monkeypatch)
        get_settings.cache_clear()
        assert get_settings().app_env == "production"
    finally:
        get_settings.cache_clear()
