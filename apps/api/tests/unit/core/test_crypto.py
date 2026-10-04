"""BACKEND-009：app/core/crypto.py（HKDF 金鑰衍生與 AES-256-GCM 應用層加解密）。"""

from collections.abc import Iterator

import pytest

from app.core import crypto
from app.core.config import get_settings
from app.core.crypto import (
    LABEL_FIELD_ENC,
    LABEL_HMAC_BINDING_CODE,
    LABEL_HMAC_ID_NUMBER,
    LABEL_JWT,
    DecryptionError,
    decrypt_bytes,
    decrypt_token,
    derive_key,
    encrypt_bytes,
    encrypt_token,
)

_SECRET_A = "a" * 48
_SECRET_B = "b" * 48
_ID_NUMBER = "A123456789"
_NONCE_LEN = 12
_TAG_LEN = 16


def _set_env(monkeypatch: pytest.MonkeyPatch, secret: str) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@127.0.0.1:54342/postgres")
    monkeypatch.setenv("APP_SECRET_KEY", secret)
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://127.0.0.1:5341")
    monkeypatch.setenv("R2_ENDPOINT_URL", "http://127.0.0.1:54344")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "afterschool")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "afterschool-local-secret")
    monkeypatch.setenv("R2_BUCKET", "afterschool-local")
    get_settings.cache_clear()
    derive_key.cache_clear()


@pytest.fixture(autouse=True)
def secret_a(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    _set_env(monkeypatch, _SECRET_A)
    yield
    get_settings.cache_clear()
    derive_key.cache_clear()


@pytest.mark.parametrize("plaintext", [_ID_NUMBER, "過敏：花生", ""])
def test_crypto_roundtrip(plaintext: str) -> None:
    blob = encrypt_bytes(plaintext)

    assert isinstance(blob, bytes)
    assert decrypt_bytes(blob) == plaintext


def test_crypto_random_nonce() -> None:
    first = encrypt_bytes(_ID_NUMBER)
    second = encrypt_bytes(_ID_NUMBER)

    assert first != second
    assert first[0] == 1
    assert len(first) == 1 + _NONCE_LEN + len(_ID_NUMBER.encode()) + _TAG_LEN
    assert first[1 : 1 + _NONCE_LEN] != second[1 : 1 + _NONCE_LEN]
    assert decrypt_bytes(second) == _ID_NUMBER


def test_crypto_tamper_detected() -> None:
    blob = encrypt_bytes(_ID_NUMBER)

    tampered_tail = blob[:-1] + bytes([blob[-1] ^ 0x01])
    with pytest.raises(DecryptionError):
        decrypt_bytes(tampered_tail)

    tampered_body = blob[:14] + bytes([blob[14] ^ 0x01]) + blob[15:]
    with pytest.raises(DecryptionError):
        decrypt_bytes(tampered_body)

    wrong_version = bytes([0x02]) + blob[1:]
    with pytest.raises(DecryptionError):
        decrypt_bytes(wrong_version)

    with pytest.raises(DecryptionError):
        decrypt_bytes(blob[:10])

    with pytest.raises(DecryptionError):
        decrypt_bytes(b"")


def test_crypto_wrong_key(monkeypatch: pytest.MonkeyPatch) -> None:
    blob = encrypt_bytes(_ID_NUMBER)

    _set_env(monkeypatch, _SECRET_B)

    with pytest.raises(DecryptionError) as exc_info:
        decrypt_bytes(blob)
    # 例外訊息不帶金鑰與明文
    text = str(exc_info.value) + repr(exc_info.value)
    assert _SECRET_A not in text
    assert _SECRET_B not in text
    assert _ID_NUMBER not in text


def test_crypto_token_format() -> None:
    token = encrypt_token("channel-token-xyz")

    assert token.startswith("v1:")
    assert "=" not in token
    assert decrypt_token(token) == "channel-token-xyz"
    assert encrypt_token("channel-token-xyz") != token

    with pytest.raises(DecryptionError):
        decrypt_token("plain")
    with pytest.raises(DecryptionError):
        decrypt_token("v2:" + token[3:])
    with pytest.raises(DecryptionError):
        decrypt_token("v1:not-base64!!")
    with pytest.raises(DecryptionError):
        decrypt_token("v1:" + token[3:-2] + "zz")


def test_crypto_labels_distinct() -> None:
    keys = [
        derive_key(LABEL_FIELD_ENC),
        derive_key(LABEL_HMAC_ID_NUMBER),
        derive_key(LABEL_HMAC_BINDING_CODE),
        derive_key(LABEL_JWT),
    ]

    assert all(len(k) == 32 for k in keys)
    assert len(set(keys)) == 4
    # 同 label 穩定（快取）
    assert derive_key(LABEL_FIELD_ENC) == keys[0]
    assert derive_key(LABEL_FIELD_ENC) is derive_key(LABEL_FIELD_ENC)


def test_crypto_derive_key_changes_with_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    key_a = derive_key(LABEL_FIELD_ENC)
    _set_env(monkeypatch, _SECRET_B)

    assert derive_key(LABEL_FIELD_ENC) != key_a


def test_crypto_key_not_in_module_repr() -> None:
    derive_key(LABEL_FIELD_ENC)

    assert _SECRET_A not in repr(vars(crypto))
