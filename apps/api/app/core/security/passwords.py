"""BACKEND-031：argon2id 密碼雜湊、恆定時間驗證、needs_rehash。

- hash 一律 ``$argon2id$`` 開頭（DB-004 的 CHECK ``password_hash like '$argon2id$%'``）。
- hashed 格式非法時先對預先算好的假 hash 做一次 verify，再回 False，
  讓「帳號不存在 / hash 壞掉」與「密碼錯誤」的回應時間一致（移植 ivy ``_dummy_hash``）。
- 本模組不記錄任何明文或 hash 到 log。
"""

from __future__ import annotations

import contextlib
import secrets

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

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
    if not hashed.startswith(_HASH_PREFIX):
        dummy_verify(plain)
        return False
    try:
        return _hasher.verify(hashed, plain)
    except VerifyMismatchError:
        return False
    except InvalidHashError:
        # 前綴正確但內容壞掉：解析階段就會失敗、沒跑到運算，補一次 dummy 維持時間一致
        dummy_verify(plain)
        return False
    except VerificationError:
        return False


def needs_rehash(hashed: str) -> bool:
    """參數弱於目前設定（或格式損毀）→ True，登入成功後應重新雜湊。"""
    if not hashed.startswith(_HASH_PREFIX):
        return True
    try:
        return _hasher.check_needs_rehash(hashed)
    except InvalidHashError:
        return True
