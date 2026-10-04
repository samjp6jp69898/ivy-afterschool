"""BACKEND-009：APP_SECRET_KEY 金鑰衍生（HKDF-SHA256）與 AES-256-GCM 應用層加解密。

學生身分證字號 / 健康備註（bytea，DB-014）與 system_settings 的 secret 欄位（JSON 字串，DB-007）
共用此模組；DB 不持金鑰，只有應用層能解密。

- ``derive_key(label)``：HKDF-SHA256，IKM = ``APP_SECRET_KEY`` UTF-8 bytes、salt=None、info=label、
  32 bytes；``lru_cache`` 快取（Settings 變更時測試呼叫 ``derive_key.cache_clear()``）。
- ``encrypt_bytes`` / ``decrypt_bytes``：``b'\\x01' + nonce(12) + ciphertext||tag``，
  AAD = ``b'afterschool'``。
- ``encrypt_token`` / ``decrypt_token``：``'v1:' + base64url(encrypt_bytes(...))``（無 padding）。
- 任何解密失敗（版本、長度、tag、base64）一律 ``DecryptionError``，訊息固定、不含金鑰與密文。
- 函式不接受 ``None``：欄位為空時由呼叫端自行判斷不加密。

金鑰輪替（APP_SECRET_KEY 變更）會使所有加密欄位與 settings secret 無法解密，目前不提供 re-encrypt。
"""

from __future__ import annotations

import base64
import binascii
import os
from functools import lru_cache
from typing import Final

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.core.config import get_settings

LABEL_FIELD_ENC: Final = b"afterschool/field-encryption/v1"
LABEL_HMAC_ID_NUMBER: Final = b"afterschool/hmac/id-number/v1"
LABEL_HMAC_BINDING_CODE: Final = b"afterschool/hmac/binding-code/v1"
LABEL_JWT: Final = b"afterschool/jwt/v1"

_KEY_LEN: Final = 32
_VERSION: Final = b"\x01"
_NONCE_LEN: Final = 12
_TAG_LEN: Final = 16
_AAD: Final = b"afterschool"
_TOKEN_PREFIX: Final = "v1:"  # noqa: S105  格式版本前綴，不是密碼
_ERROR_MESSAGE: Final = "無法解密資料"


class DecryptionError(Exception):
    """解密失敗（版本、長度、tag 驗證或編碼錯誤）；訊息固定，不帶金鑰與密文。"""

    def __init__(self) -> None:
        super().__init__(_ERROR_MESSAGE)


@lru_cache(maxsize=8)
def derive_key(label: bytes) -> bytes:
    ikm = get_settings().app_secret_key.get_secret_value().encode("utf-8")
    hkdf = HKDF(algorithm=hashes.SHA256(), length=_KEY_LEN, salt=None, info=label)
    return hkdf.derive(ikm)


def encrypt_bytes(plaintext: str) -> bytes:
    nonce = os.urandom(_NONCE_LEN)
    sealed = AESGCM(derive_key(LABEL_FIELD_ENC)).encrypt(nonce, plaintext.encode("utf-8"), _AAD)
    return _VERSION + nonce + sealed


def decrypt_bytes(blob: bytes) -> str:
    if len(blob) < 1 + _NONCE_LEN + _TAG_LEN or blob[:1] != _VERSION:
        raise DecryptionError
    nonce = blob[1 : 1 + _NONCE_LEN]
    sealed = blob[1 + _NONCE_LEN :]
    try:
        plaintext = AESGCM(derive_key(LABEL_FIELD_ENC)).decrypt(nonce, sealed, _AAD)
    except InvalidTag:
        raise DecryptionError from None
    return plaintext.decode("utf-8")


def encrypt_token(plaintext: str) -> str:
    encoded = base64.urlsafe_b64encode(encrypt_bytes(plaintext)).decode("ascii")
    return _TOKEN_PREFIX + encoded.rstrip("=")


def decrypt_token(token: str) -> str:
    if not token.startswith(_TOKEN_PREFIX):
        raise DecryptionError
    body = token[len(_TOKEN_PREFIX) :]
    try:
        blob = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
    except (binascii.Error, ValueError):
        raise DecryptionError from None
    return decrypt_bytes(blob)
