"""BACKEND-033：app/core/security/tokens.py（access token 簽發與驗證）。
BACKEND-540：簽章合法但 claim 型別異常一律 UnauthenticatedError。

HS256、typ 區分員工 / 家長、token_version、以注入的 clock 判斷過期。
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt
import pytest

from app.core.config import get_settings
from app.core.crypto import LABEL_JWT, derive_key
from app.core.errors import UnauthenticatedError
from app.core.security.tokens import (
    ACCESS_TOKEN_TTL,
    AccessClaims,
    create_access_token,
    decode_access_token,
)
from tests.support.fake_clock import FakeClock

_SECRET = "a" * 48


@pytest.fixture(autouse=True)
def secret_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@127.0.0.1:54342/postgres")
    monkeypatch.setenv("APP_SECRET_KEY", _SECRET)
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


def _claims(fake_clock: FakeClock, **overrides: object) -> dict[str, object]:
    now = int(fake_clock.now().timestamp())
    base: dict[str, object] = {
        "sub": str(uuid4()),
        "typ": "staff",
        "tv": 1,
        "iat": now,
        "exp": now + int(ACCESS_TOKEN_TTL.total_seconds()),
        "jti": "jti-test-0001",
    }
    return {**base, **overrides}


def test_tokens_roundtrip(fake_clock: FakeClock) -> None:
    sid = uuid4()
    token = create_access_token(
        subject_type="staff", subject_id=sid, token_version=3, clock=fake_clock
    )

    claims = decode_access_token(token, expected_type="staff", clock=fake_clock)

    assert isinstance(claims, AccessClaims)
    assert claims.subject_type == "staff"
    assert claims.subject_id == sid
    assert claims.token_version == 3
    assert claims.issued_at == fake_clock.now()
    assert claims.expires_at - claims.issued_at == timedelta(minutes=15)
    assert ACCESS_TOKEN_TTL.total_seconds() == 15 * 60
    assert claims.jti
    # jti 每次簽發都不同
    other = create_access_token(
        subject_type="staff", subject_id=sid, token_version=3, clock=fake_clock
    )
    assert decode_access_token(other, expected_type="staff", clock=fake_clock).jti != claims.jti
    # 家長 token 同樣可 round-trip
    parent_token = create_access_token(
        subject_type="parent", subject_id=sid, token_version=0, clock=fake_clock
    )
    parent_claims = decode_access_token(parent_token, expected_type="parent", clock=fake_clock)
    assert parent_claims.subject_type == "parent"
    assert parent_claims.token_version == 0


def test_tokens_payload_shape(fake_clock: FakeClock) -> None:
    sid = uuid4()
    token = create_access_token(
        subject_type="staff", subject_id=sid, token_version=2, clock=fake_clock
    )

    assert jwt.get_unverified_header(token)["alg"] == "HS256"
    payload = jwt.decode(
        token, derive_key(LABEL_JWT), algorithms=["HS256"], options={"verify_exp": False}
    )
    assert set(payload) == {"sub", "typ", "tv", "iat", "exp", "jti"}
    assert payload["sub"] == str(sid)
    assert payload["typ"] == "staff"
    assert payload["tv"] == 2
    assert payload["iat"] == int(fake_clock.now().timestamp())
    assert payload["exp"] == payload["iat"] + 15 * 60


def test_tokens_expired(fake_clock: FakeClock) -> None:
    token = create_access_token(
        subject_type="staff", subject_id=uuid4(), token_version=1, clock=fake_clock
    )

    fake_clock.advance(minutes=14, seconds=59)
    assert decode_access_token(token, expected_type="staff", clock=fake_clock).token_version == 1

    fake_clock.advance(seconds=2)  # 15 分 1 秒
    with pytest.raises(UnauthenticatedError) as excinfo:
        decode_access_token(token, expected_type="staff", clock=fake_clock)
    assert excinfo.value.code == "unauthenticated"
    assert excinfo.value.status == 401


def test_tokens_type_mismatch(fake_clock: FakeClock) -> None:
    parent_token = create_access_token(
        subject_type="parent", subject_id=uuid4(), token_version=0, clock=fake_clock
    )
    staff_token = create_access_token(
        subject_type="staff", subject_id=uuid4(), token_version=0, clock=fake_clock
    )

    with pytest.raises(UnauthenticatedError):
        decode_access_token(parent_token, expected_type="staff", clock=fake_clock)
    with pytest.raises(UnauthenticatedError):
        decode_access_token(staff_token, expected_type="parent", clock=fake_clock)
    # typ 不是已知值
    unknown = jwt.encode(_claims(fake_clock, typ="admin"), derive_key(LABEL_JWT), algorithm="HS256")
    with pytest.raises(UnauthenticatedError):
        decode_access_token(unknown, expected_type="staff", clock=fake_clock)


def test_tokens_alg_none_rejected(fake_clock: FakeClock) -> None:
    token = jwt.encode(_claims(fake_clock), key="", algorithm="none")

    with pytest.raises(UnauthenticatedError):
        decode_access_token(token, expected_type="staff", clock=fake_clock)


def test_tokens_wrong_key_rejected(fake_clock: FakeClock) -> None:
    token = jwt.encode(_claims(fake_clock), "other-key-" * 4, algorithm="HS256")

    with pytest.raises(UnauthenticatedError):
        decode_access_token(token, expected_type="staff", clock=fake_clock)


def test_tokens_malformed(fake_clock: FakeClock) -> None:
    with pytest.raises(UnauthenticatedError):
        decode_access_token("abc.def", expected_type="staff", clock=fake_clock)
    with pytest.raises(UnauthenticatedError):
        decode_access_token("", expected_type="staff", clock=fake_clock)

    key = derive_key(LABEL_JWT)
    bad_sub = jwt.encode(_claims(fake_clock, sub="not-uuid"), key, algorithm="HS256")
    with pytest.raises(UnauthenticatedError):
        decode_access_token(bad_sub, expected_type="staff", clock=fake_clock)

    for missing in ("sub", "typ", "tv", "iat", "exp", "jti"):
        claims = _claims(fake_clock)
        del claims[missing]
        token = jwt.encode(claims, key, algorithm="HS256")
        with pytest.raises(UnauthenticatedError):
            decode_access_token(token, expected_type="staff", clock=fake_clock)

    bad_tv = jwt.encode(_claims(fake_clock, tv="3"), key, algorithm="HS256")
    with pytest.raises(UnauthenticatedError):
        decode_access_token(bad_tv, expected_type="staff", clock=fake_clock)


def test_tokens_claims_are_aware_datetimes(fake_clock: FakeClock) -> None:
    sid = uuid4()
    token = create_access_token(
        subject_type="staff", subject_id=sid, token_version=0, clock=fake_clock
    )

    claims = decode_access_token(token, expected_type="staff", clock=fake_clock)

    assert isinstance(claims.subject_id, UUID)
    assert claims.issued_at.tzinfo is UTC
    assert claims.expires_at == datetime(2026, 9, 1, 1, 15, tzinfo=UTC)


# --- BACKEND-540：claim 型別異常 -----------------------------------------------------------


def _signed(fake_clock: FakeClock, **overrides: object) -> str:
    return jwt.encode(_claims(fake_clock, **overrides), derive_key(LABEL_JWT), algorithm="HS256")


def test_tokens_claim_types_typ_not_str(fake_clock: FakeClock) -> None:
    for typ in (["staff"], {"a": "staff"}, 1, True):
        with pytest.raises(UnauthenticatedError):
            decode_access_token(
                _signed(fake_clock, typ=typ), expected_type="staff", clock=fake_clock
            )
    null_typ = jwt.encode(
        {**_claims(fake_clock), "typ": None}, derive_key(LABEL_JWT), algorithm="HS256"
    )
    with pytest.raises(UnauthenticatedError):
        decode_access_token(null_typ, expected_type="staff", clock=fake_clock)


def test_tokens_claim_types_exp_overflow(fake_clock: FakeClock) -> None:
    for exp, iat in ((10**20, 10**20), (10**20, 1), (1, 10**20), (-(10**20), 1)):
        token = _signed(fake_clock, exp=exp, iat=iat)
        with pytest.raises(UnauthenticatedError):
            decode_access_token(token, expected_type="staff", clock=fake_clock)
    # 非整數時間
    for exp in ("later", 1.5, [1]):
        with pytest.raises(UnauthenticatedError):
            decode_access_token(
                _signed(fake_clock, exp=exp), expected_type="staff", clock=fake_clock
            )


def test_tokens_claim_types_tv_bool(fake_clock: FakeClock) -> None:
    for tv in (True, False, 1.0, "1", [1]):
        with pytest.raises(UnauthenticatedError):
            decode_access_token(_signed(fake_clock, tv=tv), expected_type="staff", clock=fake_clock)


def test_tokens_claim_types_jti_missing_or_empty(fake_clock: FakeClock) -> None:
    claims = _claims(fake_clock)
    del claims["jti"]
    missing = jwt.encode(claims, derive_key(LABEL_JWT), algorithm="HS256")
    with pytest.raises(UnauthenticatedError):
        decode_access_token(missing, expected_type="staff", clock=fake_clock)
    for jti in ("", 123, ["x"]):
        with pytest.raises(UnauthenticatedError):
            decode_access_token(
                _signed(fake_clock, jti=jti), expected_type="staff", clock=fake_clock
            )
    # sub 不是字串
    with pytest.raises(UnauthenticatedError):
        decode_access_token(_signed(fake_clock, sub=123), expected_type="staff", clock=fake_clock)
