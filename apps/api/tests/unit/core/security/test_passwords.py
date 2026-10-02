"""BACKEND-031：argon2id 密碼雜湊、恆定時間驗證、needs_rehash。"""

import pytest
from app.core.security.passwords import (
    dummy_verify,
    hash_password,
    needs_rehash,
    verify_password,
)
from argon2 import PasswordHasher


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


def test_passwords_dummy_verify_swallows_mismatch() -> None:
    assert dummy_verify("whatever") is None
