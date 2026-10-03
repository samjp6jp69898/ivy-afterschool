"""BACKEND-031：argon2id 密碼雜湊、恆定時間驗證、needs_rehash。
BACKEND-032：密碼強度規則（domain_spec M2 固定業務規則）與臨時密碼產生。

- hash 一律 ``$argon2id$`` 開頭（DB-004 的 CHECK ``password_hash like '$argon2id$%'``）。
- hashed 格式非法時先對預先算好的假 hash 做一次 verify，再回 False，
  讓「帳號不存在 / hash 壞掉」與「密碼錯誤」的回應時間一致（移植 ivy ``_dummy_hash``）。
- 本模組不記錄任何明文或 hash 到 log。
"""

from __future__ import annotations

import contextlib
import re
import secrets
from typing import Final

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.errors import AppError

_HASH_PREFIX = "$argon2id$"

_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4, type=Type.ID)

# 模組載入時算好一次；內容隨機、不會對應任何真實帳號
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(32))


def hash_password(plain: str) -> str:
    return _hasher.hash(plain)


def dummy_verify(plain: str) -> None:
    """對假 hash 執行一次 verify，只為了消耗與正常驗證等量的時間。"""
    with contextlib.suppress(VerifyMismatchError, VerificationError, InvalidHashError):
        _hasher.verify(_DUMMY_HASH, plain)


def verify_password(plain: str, hashed: str) -> bool:
    # 前綴不對或含非 ASCII（argon2 編碼只允許 ASCII，argon2-cffi 會在 _ensure_bytes 拋
    # UnicodeEncodeError）都是格式非法：先 dummy 再 False，不讓 argon2-cffi 拋例外
    if not hashed.startswith(_HASH_PREFIX) or not hashed.isascii():
        dummy_verify(plain)
        return False
    try:
        return _hasher.verify(hashed, plain)
    except VerifyMismatchError:
        # 真的跑過 argon2 運算，只是密碼不對
        return False
    except (VerificationError, ValueError):
        # 前綴正確但內容壞掉：argon2-cffi 解碼失敗拋 VerificationError（25.1）或 ValueError
        # （InvalidHashError 是 ValueError 子類、不是 VerificationError 子類），沒跑到運算，
        # 補一次 dummy 維持時間一致
        dummy_verify(plain)
        return False


def needs_rehash(hashed: str) -> bool:
    """參數弱於目前設定（或格式損毀）→ True，登入成功後應重新雜湊。"""
    if not hashed.startswith(_HASH_PREFIX):
        return True
    try:
        return _hasher.check_needs_rehash(hashed)
    except InvalidHashError:
        return True


PASSWORD_MIN_LENGTH: Final = 10
PASSWORD_MAX_LENGTH: Final = 128

# 比對時不分大小寫；長度不足的項目仍列入，讓原因清單同時指出「過於常見」
COMMON_PASSWORDS: Final = frozenset(
    {
        "password1",
        "password123",
        "passw0rd123",
        "12345678ab",
        "abcd123456",
        "abc1234567",
        "aa12345678",
        "a1234567890",
        "1234567890a",
        "qwerty123",
        "qwerty12345",
        "qwertyuiop1",
        "1qaz2wsx3edc",
        "1q2w3e4r5t",
        "zxcvbnm123",
        "iloveyou123",
        "welcome123",
        "letmein123",
        "admin12345",
        "administrator1",
    }
)

_ASCII_LETTER = re.compile(r"[A-Za-z]")
_ASCII_DIGIT = re.compile(r"[0-9]")

# 臨時密碼排除易混淆字元 0 O 1 l I
_TEMP_UPPER: Final = "ABCDEFGHJKLMNPQRSTUVWXYZ"
_TEMP_LOWER: Final = "abcdefghijkmnopqrstuvwxyz"
_TEMP_DIGITS: Final = "23456789"
_TEMP_LENGTH: Final = 12


def validate_password_strength(password: str, *, username: str | None = None) -> None:
    """不符 → AppError('weak_password', 422)，details['reasons'] 列出全部違反的規則。

    移植 ivy ``utils/auth.py::validate_password_strength`` 的條列式訊息；不做 HIBP 外部查詢。
    """
    reasons: list[str] = []
    if len(password) < PASSWORD_MIN_LENGTH:
        reasons.append(f"至少 {PASSWORD_MIN_LENGTH} 個字元")
    if len(password) > PASSWORD_MAX_LENGTH:
        reasons.append(f"最多 {PASSWORD_MAX_LENGTH} 個字元")
    if not _ASCII_LETTER.search(password):
        reasons.append("至少一個英文字母")
    if not _ASCII_DIGIT.search(password):
        reasons.append("至少一個數字")
    if username and password.casefold() == username.casefold():
        reasons.append("不可與帳號相同")
    if password.casefold() in COMMON_PASSWORDS:
        reasons.append("過於常見")
    if reasons:
        raise AppError(
            "weak_password",
            "密碼強度不足：" + "、".join(reasons),
            status=422,
            details={"reasons": reasons},
        )


def generate_temp_password() -> str:
    """12 碼臨時密碼：至少各一個大寫、小寫、數字，不含易混淆字元，以 secrets 產生。"""
    pool = _TEMP_UPPER + _TEMP_LOWER + _TEMP_DIGITS
    rng = secrets.SystemRandom()
    while True:
        chars = [
            secrets.choice(_TEMP_UPPER),
            secrets.choice(_TEMP_LOWER),
            secrets.choice(_TEMP_DIGITS),
        ]
        chars += [secrets.choice(pool) for _ in range(_TEMP_LENGTH - len(chars))]
        rng.shuffle(chars)
        candidate = "".join(chars)
        # 目前清單沒有 12 碼且不含易混淆字元的項目；保留重抽防日後清單擴充
        if candidate.casefold() not in COMMON_PASSWORDS:
            return candidate
