"""BACKEND-033：access token 簽發與驗證（HS256、token_version、typ）。

員工與家長共用 access token 格式，以 ``typ`` 區分，防止家長 token 用在後台（或反之）。
移植 ivy ``utils/auth.py::create_access_token`` / ``_check_token_algorithm`` / ``decode_token``；
去掉 tenant claim、kid 多金鑰輪替、jti blocklist、impersonation。

- claims：``sub``（str uuid）、``typ``、``tv``（token_version）、``iat``、``exp``、``jti``。
- 簽章金鑰 ``derive_key(LABEL_JWT)``（BACKEND-009），HS256。
- 解碼前先讀 unverified header，``alg != 'HS256'`` 一律拒絕（擋 alg=none / 混淆攻擊）。
- 過期以注入的 clock 判斷（``verify_exp`` 關閉後自行比對），讓 FakeClock 可測過期。
- 任何失敗（簽章錯、過期、typ 不符、缺 claim、sub 非 uuid）一律 ``UnauthenticatedError()``，
  不區分原因。
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final, Literal
from uuid import UUID

import jwt

from app.core.clock import Clock
from app.core.crypto import LABEL_JWT, derive_key
from app.core.errors import UnauthenticatedError

SubjectType = Literal["staff", "parent"]
ACCESS_TOKEN_TTL: Final = timedelta(minutes=15)

_ALGORITHM: Final = "HS256"
_SUBJECT_TYPES: Final = frozenset({"staff", "parent"})
_REQUIRED_CLAIMS: Final = ("sub", "typ", "tv", "iat", "exp", "jti")


@dataclass(frozen=True)
class AccessClaims:
    subject_type: SubjectType
    subject_id: UUID
    token_version: int
    issued_at: datetime
    expires_at: datetime
    jti: str


def create_access_token(
    *, subject_type: SubjectType, subject_id: UUID, token_version: int, clock: Clock
) -> str:
    issued_at = int(clock.now().timestamp())
    claims = {
        "sub": str(subject_id),
        "typ": subject_type,
        "tv": token_version,
        "iat": issued_at,
        "exp": issued_at + int(ACCESS_TOKEN_TTL.total_seconds()),
        "jti": secrets.token_urlsafe(16),
    }
    return jwt.encode(claims, derive_key(LABEL_JWT), algorithm=_ALGORITHM)


def _as_int(value: Any) -> int:
    # bool 是 int 的子類，JSON 的 true / false 不能當數字
    if isinstance(value, bool) or not isinstance(value, int):
        raise UnauthenticatedError
    return value


def _parse_claims(payload: dict[str, Any], expected_type: SubjectType) -> AccessClaims:
    typ = payload["typ"]
    if typ not in _SUBJECT_TYPES or typ != expected_type:
        raise UnauthenticatedError
    sub = payload["sub"]
    if not isinstance(sub, str):
        raise UnauthenticatedError
    try:
        subject_id = UUID(sub)
    except ValueError:
        raise UnauthenticatedError from None
    jti = payload["jti"]
    if not isinstance(jti, str) or not jti:
        raise UnauthenticatedError
    return AccessClaims(
        subject_type=typ,
        subject_id=subject_id,
        token_version=_as_int(payload["tv"]),
        issued_at=datetime.fromtimestamp(_as_int(payload["iat"]), tz=UTC),
        expires_at=datetime.fromtimestamp(_as_int(payload["exp"]), tz=UTC),
        jti=jti,
    )


def decode_access_token(token: str, *, expected_type: SubjectType, clock: Clock) -> AccessClaims:
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError:
        raise UnauthenticatedError from None
    if header.get("alg") != _ALGORITHM:
        raise UnauthenticatedError
    try:
        payload = jwt.decode(
            token,
            derive_key(LABEL_JWT),
            algorithms=[_ALGORITHM],
            options={
                "require": list(_REQUIRED_CLAIMS),
                # 時間一律由注入的 clock 判斷，不用 pyjwt 的系統時間
                "verify_exp": False,
                "verify_iat": False,
                "verify_nbf": False,
            },
        )
    except jwt.PyJWTError:
        raise UnauthenticatedError from None
    claims = _parse_claims(payload, expected_type)
    if clock.now() >= claims.expires_at:
        raise UnauthenticatedError
    return claims
