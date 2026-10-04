"""BACKEND-400：app/services/pickup/codes.py（接送碼產生、HMAC 存放、恆定時間比對）。"""

import re
from collections.abc import Iterator

import pytest

from app.core.config import get_settings
from app.core.crypto import LABEL_HMAC_BINDING_CODE, LABEL_HMAC_PICKUP_CODE, derive_key, keyed_hash
from app.services.pickup.codes import (
    PICKUP_CODE_MAX_ATTEMPTS,
    generate_pickup_code,
    hash_pickup_code,
    normalize_pickup_code,
    pickup_code_last4,
    pickup_code_matches,
)

_CODE_RE = re.compile(r"[1-9]\d{5}")


@pytest.fixture(autouse=True)
def secret_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@127.0.0.1:54342/postgres")
    monkeypatch.setenv("APP_SECRET_KEY", "a" * 48)
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://127.0.0.1:5341")
    monkeypatch.setenv("R2_ENDPOINT_URL", "http://127.0.0.1:54344")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "afterschool")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "afterschool-local-secret")
    monkeypatch.setenv("R2_BUCKET", "afterschool-local")
    get_settings.cache_clear()
    derive_key.cache_clear()
    yield
    get_settings.cache_clear()
    derive_key.cache_clear()


def test_pickup_codes_generate() -> None:
    codes = {generate_pickup_code() for _ in range(1000)}

    assert all(_CODE_RE.fullmatch(code) for code in codes)
    assert all(100000 <= int(code) <= 999999 for code in codes)
    # 1000 次不會全部一樣（亂數）
    assert len(codes) > 1
    assert PICKUP_CODE_MAX_ATTEMPTS == 5


def test_pickup_codes_hash_format() -> None:
    digest = hash_pickup_code("123456")

    assert re.fullmatch(r"[0-9a-f]{64}", digest)
    assert hash_pickup_code("123456") == digest
    assert hash_pickup_code("123 456") == digest
    assert hash_pickup_code("654321") != digest
    for bad in ("12345", "1234567", "12345a", "", "abcdef"):
        with pytest.raises(ValueError, match="接送碼"):
            hash_pickup_code(bad)


def test_pickup_codes_normalize() -> None:
    assert normalize_pickup_code("123 456") == "123456"
    assert normalize_pickup_code(" 123-456 ") == "123456"
    assert normalize_pickup_code("\uff11\uff12\uff13\uff14\uff15\uff16") is None  # 全形數字
    assert normalize_pickup_code("12345") is None
    assert normalize_pickup_code("abc") is None
    assert normalize_pickup_code("") is None


def test_pickup_codes_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.pickup import codes as codes_module

    digest = hash_pickup_code("123456")

    assert pickup_code_matches("123 456", digest) is True
    assert pickup_code_matches("123-456", digest) is True
    assert pickup_code_matches("123456", digest) is True
    assert pickup_code_matches("654321", digest) is False
    assert pickup_code_matches("abc", digest) is False
    assert pickup_code_matches("", digest) is False
    assert pickup_code_matches("123456", "") is False

    calls: list[tuple[str, str]] = []

    def _spy(a: str, b: str) -> bool:
        calls.append((a, b))
        return a == b

    monkeypatch.setattr(codes_module, "constant_time_equals", _spy)
    assert pickup_code_matches("123456", digest) is True
    assert calls == [(digest, digest)]


def test_pickup_codes_label_isolation() -> None:
    assert LABEL_HMAC_PICKUP_CODE == b"afterschool/hmac/pickup-code/v1"
    assert keyed_hash(LABEL_HMAC_PICKUP_CODE, "123456") != keyed_hash(
        LABEL_HMAC_BINDING_CODE, "123456"
    )
    assert hash_pickup_code("123456") == keyed_hash(LABEL_HMAC_PICKUP_CODE, "123456")
    assert pickup_code_last4("123456") == "3456"
    assert pickup_code_last4("123-456") == "3456"
    with pytest.raises(ValueError, match="接送碼"):
        pickup_code_last4("12")
