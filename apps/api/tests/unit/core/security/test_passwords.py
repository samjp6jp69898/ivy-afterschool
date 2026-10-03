"""BACKEND-031：argon2id 密碼雜湊、恆定時間驗證、needs_rehash。
BACKEND-032：validate_password_strength、generate_temp_password。
"""

import pytest
from argon2 import PasswordHasher

from app.core.errors import AppError
from app.core.security.passwords import (
    COMMON_PASSWORDS,
    dummy_verify,
    generate_temp_password,
    hash_password,
    needs_rehash,
    validate_password_strength,
    verify_password,
)


def test_passwords_hash_format() -> None:
    first = hash_password("Passw0rd-Test1")
    second = hash_password("Passw0rd-Test1")

    assert first.startswith("$argon2id$")
    assert second.startswith("$argon2id$")
    assert first != second


def test_passwords_verify() -> None:
    hashed = hash_password("Passw0rd-Test1")

    assert verify_password("Passw0rd-Test1", hashed) is True
    assert verify_password("passw0rd-test1", hashed) is False


def test_passwords_verify_invalid_hash() -> None:
    assert verify_password("x", "not-a-hash") is False
    assert verify_password("x", "") is False
    # 前綴正確但內容壞掉也不可拋例外
    assert verify_password("x", "$argon2id$v=19$m=65536,t=3,p=4$broken") is False


def test_passwords_needs_rehash() -> None:
    weak = PasswordHasher(time_cost=1).hash("x")

    assert needs_rehash(weak) is True
    assert needs_rehash(hash_password("x")) is False
    assert needs_rehash("not-a-hash") is True


def test_passwords_dummy_verify_runs_argon2(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []
    original = PasswordHasher.verify

    def counting(self: PasswordHasher, hashed: str | bytes, password: str | bytes) -> bool:
        calls.append((str(hashed), str(password)))
        return original(self, hashed, password)

    monkeypatch.setattr(PasswordHasher, "verify", counting)

    assert verify_password("x", "not-a-hash") is False

    assert len(calls) == 1
    # 真的對預先算好的 argon2id 假 hash 做了一次驗證，而不是對非法字串
    assert calls[0][0].startswith("$argon2id$")
    assert calls[0][1] == "x"


@pytest.mark.parametrize(
    "hashed",
    [
        "$argon2id$v=19$m=65536,t=3,p=4$broken",
        "$argon2id$",
        # 非 ASCII：argon2-cffi 在 _ensure_bytes 階段拋 UnicodeEncodeError（ValueError 子類）；
        # 全形字母就是要測混淆字元，不是打錯
        "$argon2id$v=19$m=65536,t=3,p=4$ＡＢＣ$ＤＥＦ",  # noqa: RUF001
        "$argon2id$é",
    ],
)
def test_passwords_dummy_verify_runs_for_malformed_prefix(
    monkeypatch: pytest.MonkeyPatch, hashed: str
) -> None:
    # 前綴正確但解碼失敗（VerificationError 或 ValueError）也要跑一次假 hash 驗證、不拋例外，
    # 不可 0 ms 就回 False（時間側通道）
    calls: list[str] = []
    original = PasswordHasher.verify

    def counting(self: PasswordHasher, h: str | bytes, password: str | bytes) -> bool:
        calls.append(str(h))
        return original(self, h, password)

    monkeypatch.setattr(PasswordHasher, "verify", counting)

    assert verify_password("x", hashed) is False

    dummy_calls = [h for h in calls if h != hashed]
    assert len(dummy_calls) == 1
    assert dummy_calls[0].startswith("$argon2id$")


def test_passwords_dummy_verify_swallows_mismatch() -> None:
    # 假 hash 一定對不上任何明文，verify 的 mismatch 不可外洩成例外
    dummy_verify("whatever")


# --- BACKEND-032 ------------------------------------------------------------------------


def _reasons(password: str, username: str | None = None) -> list[str]:
    with pytest.raises(AppError) as exc_info:
        validate_password_strength(password, username=username)
    exc = exc_info.value
    assert exc.code == "weak_password"
    assert exc.status == 422
    reasons = exc.details["reasons"]
    assert exc.message == "密碼強度不足：" + "、".join(reasons)
    return list(reasons)


def test_password_strength_too_short() -> None:
    assert _reasons("abc123") == ["至少 10 個字元"]
    # 邊界：9 碼不行、10 碼可以
    assert _reasons("abcdefgh1") == ["至少 10 個字元"]
    validate_password_strength("abcdefgh12")


def test_password_strength_too_long() -> None:
    validate_password_strength("a1" * 64)
    assert _reasons("a1" * 64 + "b") == ["最多 128 個字元"]


def test_password_strength_needs_letter_and_digit() -> None:
    assert _reasons("abcdefghijk") == ["至少一個數字"]
    assert _reasons("1234567890") == ["至少一個英文字母"]
    # 中文字與全形數字不算英文字母 / 數字
    assert _reasons("密碼密碼密碼密碼密碼1") == ["至少一個英文字母"]
    assert _reasons("abcdefghij１２３") == ["至少一個數字"]  # noqa: RUF001  刻意用全形數字


def test_password_strength_not_username() -> None:
    assert _reasons("Lin.Teacher1", username="lin.teacher1") == ["不可與帳號相同"]
    # 沒有帳號或帳號不同時不檢查
    validate_password_strength("Lin.Teacher1")
    validate_password_strength("Lin.Teacher1", username="lin.teacher2")
    validate_password_strength("Lin.Teacher1", username="")


def test_password_strength_common_password() -> None:
    assert len(COMMON_PASSWORDS) == 20
    assert {"password1", "12345678ab", "qwerty123", "password123"} <= COMMON_PASSWORDS
    assert _reasons("password123") == ["過於常見"]
    # 不分大小寫
    assert _reasons("PassWord123") == ["過於常見"]


def test_password_strength_lists_all_reasons() -> None:
    assert _reasons("") == ["至少 10 個字元", "至少一個英文字母", "至少一個數字"]
    assert _reasons("abc", username="ABC") == ["至少 10 個字元", "至少一個數字", "不可與帳號相同"]
    assert _reasons("password1") == ["至少 10 個字元", "過於常見"]


def test_password_strength_ok() -> None:
    validate_password_strength("Afterschool2026")
    validate_password_strength("Afterschool2026", username="lin.teacher")


def test_password_temp_generator() -> None:
    generated = [generate_temp_password() for _ in range(50)]

    for pw in generated:
        assert len(pw) == 12
        assert not set(pw) & set("0OlI1")
        assert any(c.isascii() and c.isupper() for c in pw)
        assert any(c.isascii() and c.islower() for c in pw)
        assert any(c.isascii() and c.isdigit() for c in pw)
        assert pw.isascii()
        assert pw.isalnum()
        validate_password_strength(pw)
    # secrets 產生：50 組不會重複
    assert len(set(generated)) == 50
