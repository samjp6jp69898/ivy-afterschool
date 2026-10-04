"""BACKEND-400：接送碼工具（domain_spec M7 / DB-024）。

接送碼只存 ``code_hash``（HMAC-SHA256，``LABEL_HMAC_PICKUP_CODE``）與 ``code_last4``，明碼只在建立
當下回傳一次。員工端核對家長轉告的完整碼，不需要顯示明碼，所以把 ivy ``services/pickup_code.py``
的可逆 AES-GCM 與多金鑰輪替改為單向 HMAC；``generate_pickup_code`` 沿用 6 位數、避開前導 0。
"""

from __future__ import annotations

import re
import secrets
from typing import Final

from app.core.crypto import LABEL_HMAC_PICKUP_CODE, constant_time_equals, keyed_hash

PICKUP_CODE_MAX_ATTEMPTS: Final = 5
_CODE_RE: Final = re.compile(r"^[0-9]{6}$")
_STRIP_RE: Final = re.compile(r"[\s-]+")


def generate_pickup_code() -> str:
    """100000 ~ 999999 的 6 位數字字串。"""
    return str(secrets.randbelow(900000) + 100000)


def normalize_pickup_code(raw: str) -> str | None:
    """去空白與 '-'；不是 6 位 ASCII 數字 → None。"""
    code = _STRIP_RE.sub("", raw)
    return code if _CODE_RE.fullmatch(code) and code.isascii() else None


def _require_code(raw: str) -> str:
    code = normalize_pickup_code(raw)
    if code is None:
        raise ValueError("接送碼必須是 6 位數字")
    return code


def hash_pickup_code(code: str) -> str:
    return keyed_hash(LABEL_HMAC_PICKUP_CODE, _require_code(code))


def pickup_code_matches(raw: str, code_hash: str) -> bool:
    code = normalize_pickup_code(raw)
    if code is None:
        return False
    return constant_time_equals(keyed_hash(LABEL_HMAC_PICKUP_CODE, code), code_hash)


def pickup_code_last4(code: str) -> str:
    return _require_code(code)[-4:]
