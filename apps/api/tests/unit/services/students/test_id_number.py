"""BACKEND-147：app/services/students/id_number.py（身分證正規化、檢查碼、HMAC、遮罩）。"""

import re
from collections.abc import Iterator

import pytest

from app.core.config import get_settings
from app.core.crypto import LABEL_HMAC_ID_NUMBER, derive_key, keyed_hash
from app.core.errors import AppError
from app.services.students.id_number import (
    id_number_hmac,
    mask_id_number,
    normalize_id_number,
    validate_id_number,
)


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


def _assert_invalid(value: str) -> None:
    with pytest.raises(AppError) as excinfo:
        validate_id_number(value)
    assert excinfo.value.status == 422
    assert excinfo.value.code == "invalid_id_number"
    assert excinfo.value.message == "身分證字號格式不正確"


def test_id_number_normalize() -> None:
    assert normalize_id_number(" a123 456 789 ") == "A123456789"
    assert normalize_id_number("a1234\t5678\n9") == "A123456789"
    assert normalize_id_number("") == ""


def test_id_number_normalize_nfkc_fullwidth() -> None:
    """全形英數（中文輸入法常見）先 NFKC 轉半形，HMAC 才會與半形輸入一致。"""
    fullwidth_tail = "A12345678\uff19"  # 末碼為全形數字 9（U+FF19）
    fullwidth_second = "A\uff11" + "23456789"  # 第二碼為全形數字 1（U+FF11）
    fullwidth_letter = "\uff21123456789"  # 首碼為全形字母 A（U+FF21）

    for raw in (
        fullwidth_tail,
        fullwidth_second,
        fullwidth_letter,
        " \uff41\uff11\uff12\uff13 456 789 ",
    ):
        normalized = normalize_id_number(raw)
        assert normalized == "A123456789", raw
        assert normalized.isascii()
        validate_id_number(normalized)
        assert id_number_hmac(normalized) == id_number_hmac("A123456789")


def test_id_number_rejects_non_ascii_digits() -> None:
    """NFKC 後仍非 ASCII 的數字（阿拉伯-印度數字等）一律 422，不得通過 \\d / int() 的寬鬆比對。"""
    arabic_indic = "A123456\u0667" + "89"  # 阿拉伯-印度數字 7（U+0667）

    assert normalize_id_number(arabic_indic) == arabic_indic
    _assert_invalid(arabic_indic)
    _assert_invalid(normalize_id_number(arabic_indic))
    # 未經 normalize 的全形輸入直接驗證也要 422（驗證只收 ASCII）
    _assert_invalid("A12345678\uff19")
    _assert_invalid("\uff21123456789")
    _assert_invalid("A1234567\u0668\u0669")  # ٨٩


def test_id_number_valid_citizen() -> None:
    # 檢查碼正確的公開範例
    validate_id_number("A123456789")
    validate_id_number("F131104093")
    validate_id_number("B220000000")


def test_id_number_bad_checksum() -> None:
    _assert_invalid("A123456788")
    _assert_invalid("A123456780")


@pytest.mark.parametrize(
    "value",
    [
        "",
        "A12345678",
        "A1234567890",
        "a123456789",
        "A123456 789",
        "1123456789",
        "AA23456789",
        "A523456789",
    ],
)
def test_id_number_bad_format(value: str) -> None:
    _assert_invalid(value)


def test_id_number_resident_certs() -> None:
    validate_id_number("A800000014")  # 新式居留證
    validate_id_number("AC01234567")  # 舊式居留證
    _assert_invalid("A300000000")
    _assert_invalid("A800000015")  # 新式檢查碼錯
    _assert_invalid("AC01234568")  # 舊式檢查碼錯
    _assert_invalid("AE01234567")  # 舊式第二碼只接受 A-D


def test_id_number_hmac_and_mask() -> None:
    digest = id_number_hmac("A123456789")

    assert re.fullmatch(r"[0-9a-f]{64}", digest)
    assert id_number_hmac("A123456789") == digest
    assert digest == keyed_hash(LABEL_HMAC_ID_NUMBER, "A123456789")
    assert id_number_hmac("A123456780") != digest
    assert mask_id_number("A123456789") == "A12****789"
    assert mask_id_number("AC01234567") == "AC0****567"
