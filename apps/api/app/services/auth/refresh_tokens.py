"""BACKEND-036：refresh token 發行（員工與家長共用，DB-005 refresh_tokens）。

移植 ivy ``services/staff_refresh.py::issue_refresh_token``；去掉 tenant、user_agent / ip 欄位。
raw token 只回給呼叫端放進 cookie，DB 只存 sha256 hex。同一次登入工作階段的 token 共用
family_id（輪替時沿用），用於重用偵測時整族撤銷。
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.core.clock import Clock
from app.core.security.cookies import REFRESH_TTL
from app.core.security.tokens import SubjectType
from app.models.account import RefreshToken


@dataclass(frozen=True)
class IssuedRefresh:
    raw: str
    family_id: UUID
    token_id: UUID


def hash_refresh(raw: str) -> str:
    """sha256 hex（DB CHECK ``^[0-9a-f]{64}$``）。"""
    return hashlib.sha256(raw.encode()).hexdigest()


def issue(
    session: Session,
    *,
    subject_type: SubjectType,
    subject_id: UUID,
    clock: Clock,
    family_id: UUID | None = None,
) -> IssuedRefresh:
    """建立一筆 refresh token；family_id 未給視為新的登入工作階段。只 flush，不 commit。"""
    raw = secrets.token_urlsafe(48)
    now = clock.now()
    # created_at 也取注入時鐘：DB CHECK expires_at > created_at 必須與同一個「現在」比較
    token = RefreshToken(
        created_at=now,
        subject_type=subject_type,
        subject_id=subject_id,
        family_id=family_id or uuid4(),
        token_hash=hash_refresh(raw),
        expires_at=now + REFRESH_TTL[subject_type],
    )
    session.add(token)
    session.flush()
    return IssuedRefresh(raw=raw, family_id=token.family_id, token_id=token.id)
