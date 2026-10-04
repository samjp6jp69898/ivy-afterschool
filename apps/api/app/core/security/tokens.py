"""BACKEND-033：access token 簽發與驗證（HS256、token_version、typ）。
BACKEND-034：家長首次綁定用的受限 token（``typ='bind'``，10 分鐘）。

員工與家長共用 access token 格式，以 ``typ`` 區分，防止家長 token 用在後台（或反之）。
移植 ivy ``utils/auth.py::create_access_token`` / ``_check_token_algorithm`` / ``decode_token``；
去掉 tenant claim、kid 多金鑰輪替、jti blocklist、impersonation。

- claims：``sub``（str uuid）、``typ``、``tv``（token_version）、``iat``、``exp``、``jti``。
- 簽章金鑰 ``derive_key(LABEL_JWT)``（BACKEND-009），HS256。
- 解碼前先讀 unverified header，``alg != 'HS256'`` 一律拒絕（擋 alg=none / 混淆攻擊）。
- 過期以注入的 clock 判斷（``verify_exp`` 關閉後自行比對），讓 FakeClock 可測過期。
- 任何失敗（簽章錯、過期、typ 不符、缺 claim、sub 非 uuid）一律 ``UnauthenticatedError()``，
  不區分原因。claim 型別逐一驗證（BACKEND-540）：``typ`` / ``sub`` / ``jti`` 必須是非空 str、
  ``tv`` / ``iat`` / ``exp`` 必須是 int（排除 bool），timestamp 轉換的 OverflowError / OSError /
  ValueError 也轉成 UnauthenticatedError；不以 ``except Exception`` 吞掉程式錯誤。
- 綁定 token 與 access token 同一把金鑰、同一條解碼路徑，以 ``typ`` 互斥：bind token 不能當
  access token（``expected_type`` 只接受 staff / parent），access token 也不能當 bind token。
  ``line_user_id`` 必須符合 ``^U[0-9a-f]{32}$``。移植 ivy ``api/parent_portal/auth.py``
  ``_issue_bind_temp_token`` / ``_decode_bind_temp_token``；去掉 tenant。
"""

from __future__ import annotations

import re
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
BIND_TOKEN_TTL: Final = timedelta(minutes=10)

_ALGORITHM: Final = "HS256"
_SUBJECT_TYPES: Final = frozenset({"staff", "parent"})
_REQUIRED_CLAIMS: Final = ("sub", "typ", "tv", "iat", "exp", "jti")
_BIND_TYP: Final = "bind"
# display_name / picture_url 可為 null，pyjwt 的 require 會把 null 當缺漏，不列入必要 claim
_BIND_REQUIRED_CLAIMS: Final = ("typ", "line_user_id", "iat", "exp", "jti")
_LINE_USER_ID_RE: Final = re.compile(r"^U[0-9a-f]{32}$")


@dataclass(frozen=True)
class AccessClaims:
    subject_type: SubjectType
    subject_id: UUID
    token_version: int
    issued_at: datetime
    expires_at: datetime
    jti: str


@dataclass(frozen=True)
class BindClaims:
    line_user_id: str
    display_name: str | None
    picture_url: str | None


def _timestamps(clock: Clock, ttl: timedelta) -> tuple[int, int]:
    issued_at = int(clock.now().timestamp())
    return issued_at, issued_at + int(ttl.total_seconds())


def create_access_token(
    *, subject_type: SubjectType, subject_id: UUID, token_version: int, clock: Clock
) -> str:
    issued_at, expires_at = _timestamps(clock, ACCESS_TOKEN_TTL)
    claims = {
        "sub": str(subject_id),
        "typ": subject_type,
        "tv": token_version,
        "iat": issued_at,
        "exp": expires_at,
        "jti": secrets.token_urlsafe(16),
    }
    return jwt.encode(claims, derive_key(LABEL_JWT), algorithm=_ALGORITHM)


def create_bind_token(claims: BindClaims, *, clock: Clock) -> str:
    if not _LINE_USER_ID_RE.fullmatch(claims.line_user_id):
        raise ValueError("line_user_id 格式不正確（^U[0-9a-f]{32}$）")
    issued_at, expires_at = _timestamps(clock, BIND_TOKEN_TTL)
    payload = {
        "typ": _BIND_TYP,
        "line_user_id": claims.line_user_id,
        "display_name": claims.display_name,
        "picture_url": claims.picture_url,
        "iat": issued_at,
        "exp": expires_at,
        "jti": secrets.token_urlsafe(16),
    }
    return jwt.encode(payload, derive_key(LABEL_JWT), algorithm=_ALGORITHM)


def _require_str(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise UnauthenticatedError
    return value


def _optional_str(value: Any) -> str | None:
    if value is not None and not isinstance(value, str):
        raise UnauthenticatedError
    return value


def _as_int(value: Any) -> int:
    # bool 是 int 的子類，JSON 的 true / false 不能當數字
    if isinstance(value, bool) or not isinstance(value, int):
        raise UnauthenticatedError
    return value


def _as_datetime(value: Any) -> datetime:
    # 超大 / 負數 timestamp 會拋 OverflowError / OSError / ValueError，一律視為無效 token
    try:
        return datetime.fromtimestamp(_as_int(value), tz=UTC)
    except (OverflowError, OSError, ValueError):
        raise UnauthenticatedError from None


def _decode_payload(token: str, *, typ: str, required: tuple[str, ...]) -> dict[str, Any]:
    """驗簽章（HS256）與必要 claim 後回傳 payload；``typ`` 不符或任何失敗 → UnauthenticatedError。

    時間 claim 不在此驗證（交給呼叫端以注入的 clock 判斷）。
    """
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
                "require": list(required),
                # 時間一律由注入的 clock 判斷，不用 pyjwt 的系統時間
                "verify_exp": False,
                "verify_iat": False,
                "verify_nbf": False,
            },
        )
    except jwt.PyJWTError:
        raise UnauthenticatedError from None
    if not isinstance(payload, dict) or payload.get("typ") != typ:
        raise UnauthenticatedError
    return payload


def _parse_claims(payload: dict[str, Any], expected_type: SubjectType) -> AccessClaims:
    typ = _require_str(payload["typ"])
    if typ != expected_type:
        raise UnauthenticatedError
    try:
        subject_id = UUID(_require_str(payload["sub"]))
    except ValueError:
        raise UnauthenticatedError from None
    return AccessClaims(
        subject_type=expected_type,
        subject_id=subject_id,
        token_version=_as_int(payload["tv"]),
        issued_at=_as_datetime(payload["iat"]),
        expires_at=_as_datetime(payload["exp"]),
        jti=_require_str(payload["jti"]),
    )


def decode_access_token(token: str, *, expected_type: SubjectType, clock: Clock) -> AccessClaims:
    if expected_type not in _SUBJECT_TYPES:
        raise ValueError(f"expected_type 只接受 staff / parent：{expected_type!r}")
    payload = _decode_payload(token, typ=expected_type, required=_REQUIRED_CLAIMS)
    claims = _parse_claims(payload, expected_type)
    if clock.now() >= claims.expires_at:
        raise UnauthenticatedError
    return claims


def decode_bind_token(token: str, *, clock: Clock) -> BindClaims:
    payload = _decode_payload(token, typ=_BIND_TYP, required=_BIND_REQUIRED_CLAIMS)
    line_user_id = _require_str(payload["line_user_id"])
    if not _LINE_USER_ID_RE.fullmatch(line_user_id):
        raise UnauthenticatedError
    _require_str(payload["jti"])
    _as_datetime(payload["iat"])
    if clock.now() >= _as_datetime(payload["exp"]):
        raise UnauthenticatedError
    return BindClaims(
        line_user_id=line_user_id,
        display_name=_optional_str(payload.get("display_name")),
        picture_url=_optional_str(payload.get("picture_url")),
    )
