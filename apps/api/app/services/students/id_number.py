"""BACKEND-147：身分證字號正規化、格式 / 檢查碼驗證、HMAC 查重鍵與遮罩（DB-014）。

- ``normalize_id_number``：先 NFKC（全形英數轉半形，中文輸入法常打出全形）、去除所有空白、轉大寫。
  呼叫端一律先正規化再做任何事，確保 HMAC 查重對同一值永遠用同一正規化（BACKEND-010）。
- 驗證只收 ASCII：regex 用 ``[0-9]`` / ``[A-Z]``，NFKC 後仍非 ASCII 的數字（阿拉伯-印度數字等）
  一律 422，避免 ``\\d`` / ``int()`` 接受 Unicode 數字造成同一人以不同 HMAC 重複建檔。
- ``validate_id_number`` 接受三種格式（皆驗檢查碼）：國民身分證 ``^[A-Z][12][0-9]{8}$``、
  新式居留證 ``^[A-Z][89][0-9]{8}$``、舊式居留證 ``^[A-Z][A-D][0-9]{8}$``。檢查碼：首碼字母依內政部
  對照表換成兩位數（A=10 … I=34、O=35 等特例），舊式居留證第二碼字母取對照數的個位數；
  加權 1,9,8,7,6,5,4,3,2,1,1 總和 mod 10 == 0。
- 不支援護照號碼（外籍學生若只有護照號碼會被擋下，見 task risk_notes）。
"""

from __future__ import annotations

import re
import unicodedata
from typing import Final

from app.core.crypto import LABEL_HMAC_ID_NUMBER, keyed_hash
from app.core.errors import AppError

INVALID_ID_NUMBER_CODE: Final = "invalid_id_number"
INVALID_ID_NUMBER_MESSAGE: Final = "身分證字號格式不正確"

# 內政部英文字母對照表
_LETTER_VALUES: Final[dict[str, int]] = {
    "A": 10, "B": 11, "C": 12, "D": 13, "E": 14, "F": 15, "G": 16, "H": 17, "I": 34, "J": 18,
    "K": 19, "L": 20, "M": 21, "N": 22, "O": 35, "P": 23, "Q": 24, "R": 25, "S": 26, "T": 27,
    "U": 28, "V": 29, "W": 32, "X": 30, "Y": 31, "Z": 33,
}  # fmt: skip
_WEIGHTS: Final = (1, 9, 8, 7, 6, 5, 4, 3, 2, 1, 1)
# 只收 ASCII：\d 會比對 Unicode 數字
_CITIZEN_RE: Final = re.compile(r"^[A-Z][12][0-9]{8}$")
_NEW_RESIDENT_RE: Final = re.compile(r"^[A-Z][89][0-9]{8}$")
_OLD_RESIDENT_RE: Final = re.compile(r"^[A-Z][A-D][0-9]{8}$")
_WHITESPACE_RE: Final = re.compile(r"\s+")


def normalize_id_number(raw: str) -> str:
    return _WHITESPACE_RE.sub("", unicodedata.normalize("NFKC", raw)).upper()


def _digits(normalized: str) -> list[int]:
    """轉成 11 個數字：首碼字母兩位 + 第二碼（數字或舊式字母的個位數）+ 後 8 碼。"""
    first = _LETTER_VALUES[normalized[0]]
    second_char = normalized[1]
    second = _LETTER_VALUES[second_char] % 10 if second_char.isalpha() else int(second_char)
    return [first // 10, first % 10, second, *(int(c) for c in normalized[2:])]


def _checksum_ok(normalized: str) -> bool:
    total = sum(d * w for d, w in zip(_digits(normalized), _WEIGHTS, strict=True))
    return total % 10 == 0


def validate_id_number(normalized: str) -> None:
    """格式或檢查碼不符 → AppError 422 ``invalid_id_number``。"""
    patterns = (_CITIZEN_RE, _NEW_RESIDENT_RE, _OLD_RESIDENT_RE)
    if (
        not normalized.isascii()
        or not any(p.fullmatch(normalized) for p in patterns)
        or not _checksum_ok(normalized)
    ):
        raise AppError(INVALID_ID_NUMBER_CODE, INVALID_ID_NUMBER_MESSAGE, status=422)


def id_number_hmac(normalized: str) -> str:
    return keyed_hash(LABEL_HMAC_ID_NUMBER, normalized)


def mask_id_number(normalized: str) -> str:
    """前 3 後 3 保留，中間 4 碼遮罩：'A12****789'。"""
    return f"{normalized[:3]}****{normalized[-3:]}"
