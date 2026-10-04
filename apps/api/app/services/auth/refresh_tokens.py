"""BACKEND-036：refresh token 發行（員工與家長共用，DB-005 refresh_tokens）。
BACKEND-037：輪替 ``rotate``（重用偵測即撤銷整個 family + token_version +1、5 秒內併發容忍）。
BACKEND-038：``revoke_family_by_raw``（登出，冪等）、``revoke_all_for_subject``（改密碼 / 重設密碼 /
停用帳號）；兩者只 flush 不 commit，token_version 由呼叫端依情境處理。

移植 ivy ``services/staff_refresh.py``（issue / rotate / ``_revoke_locked``）；去掉 tenant、
absolute lifetime、user_agent / ip 欄位。
raw token 只回給呼叫端放進 cookie，DB 只存 sha256 hex。同一次登入工作階段的 token 共用
family_id（輪替時沿用），用於重用偵測時整族撤銷。

帳號狀態（停用、改密碼）的檢查由呼叫端 service 負責（BACKEND-043 / 058），本模組只處理 token 本身。
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final, cast
from uuid import UUID, uuid4

from sqlalchemy import ColumnElement, CursorResult, select, update
from sqlalchemy.orm import Session

from app.core.clock import Clock
from app.core.errors import ConflictError, UnauthenticatedError
from app.core.security.cookies import REFRESH_TTL
from app.core.security.tokens import SubjectType
from app.models.account import RefreshToken, StaffUser
from app.models.parents import ParentAccount

logger = logging.getLogger(__name__)

# 同一 token 併發的兩個 refresh 請求：後到者在此時間內視為 race 而非重用
RACE_TOLERANCE: Final = timedelta(seconds=5)

_SUBJECT_TABLES: Final[dict[SubjectType, type[StaffUser] | type[ParentAccount]]] = {
    "staff": StaffUser,
    "parent": ParentAccount,
}


@dataclass(frozen=True)
class IssuedRefresh:
    raw: str
    family_id: UUID
    token_id: UUID


@dataclass(frozen=True)
class RotatedRefresh:
    raw: str
    subject_type: SubjectType
    subject_id: UUID
    family_id: UUID


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


def _revoke_locked(session: Session, *criteria: ColumnElement[bool], now: datetime) -> int:
    """撤銷符合條件且尚未撤銷的列：先 ``select id ... for update`` 鎖列，再 bulk update。

    ivy F14：READ COMMITTED 下單一 bulk update 遇到進行中的輪替持有舊列鎖時，等鎖後只會更新
    原 statement snapshot 內的列，漏掉輪替新增的後繼 token。先鎖定（等 in-flight 輪替 commit）
    再發第二條 update，新 statement 的 snapshot 才看得到後繼列。回傳撤銷筆數。
    """
    base = (RefreshToken.revoked_at.is_(None), *criteria)
    session.execute(select(RefreshToken.id).where(*base).with_for_update()).all()
    result = session.execute(update(RefreshToken).where(*base).values(revoked_at=now))
    return int(cast(CursorResult[Any], result).rowcount or 0)


def _bump_token_version(session: Session, subject_type: SubjectType, subject_id: UUID) -> None:
    """帳號 token_version +1，讓既有 access token 全部失效（SQL 層遞增，避免 lost update）。"""
    model: Any = _SUBJECT_TABLES[subject_type]
    session.execute(
        update(model).where(model.id == subject_id).values(token_version=model.token_version + 1)
    )


def rotate(session: Session, raw: str, *, clock: Clock) -> RotatedRefresh:
    """驗證並輪替 refresh token，回傳新 raw（同 family）。

    - 不存在 → 401 ``refresh_invalid``；已撤銷 → 401 ``refresh_revoked``；
      ``expires_at <= now`` → 401 ``refresh_expired``。
    - 已被用過（``replaced_by`` 非 null）：後繼 token 建立於 5 秒內 → 409 ``refresh_in_progress``
      （併發的重複請求，前端重打即可，不撤銷）；超過 5 秒 → 重用：撤銷同 family 全部 token、
      帳號 token_version +1，**在 raise 前 commit**（service 不 commit 原則的例外只有兩處：
      BACKEND-037 rotate 重用分支、BACKEND-043 / 058 refresh 的 401 撤銷路徑；否則 get_db 的
      rollback 會把撤銷一起回滾），再 raise 401 ``refresh_reused``。
    - 正常：建立新 token、舊列 ``replaced_by`` 指向新列，只 flush。
    """
    now = clock.now()
    token = session.execute(
        select(RefreshToken).where(RefreshToken.token_hash == hash_refresh(raw)).with_for_update()
    ).scalar_one_or_none()
    if token is None:
        raise UnauthenticatedError(code="refresh_invalid")
    if token.revoked_at is not None:
        raise UnauthenticatedError(code="refresh_revoked")
    if token.expires_at <= now:
        raise UnauthenticatedError("登入已過期，請重新登入", code="refresh_expired")

    if token.replaced_by is not None:
        successor_created_at = session.execute(
            select(RefreshToken.created_at).where(RefreshToken.id == token.replaced_by)
        ).scalar_one_or_none()
        if successor_created_at is not None and now - successor_created_at <= RACE_TOLERANCE:
            raise ConflictError("refresh_in_progress", "登入狀態更新中，請重試")
        # 後繼列已不存在（被清理）或超過容忍時間：視為 token 外洩重用
        _revoke_locked(session, RefreshToken.family_id == token.family_id, now=now)
        _bump_token_version(session, token.subject_type, token.subject_id)
        session.commit()
        logger.warning(
            "refresh token 重用：撤銷整個 family subject_type=%s subject_id=%s family_id=%s",
            token.subject_type,
            token.subject_id,
            token.family_id,
        )
        raise UnauthenticatedError("登入狀態異常，請重新登入", code="refresh_reused")

    issued = issue(
        session,
        subject_type=token.subject_type,
        subject_id=token.subject_id,
        clock=clock,
        family_id=token.family_id,
    )
    token.replaced_by = issued.token_id
    session.flush()
    return RotatedRefresh(
        raw=issued.raw,
        subject_type=token.subject_type,
        subject_id=token.subject_id,
        family_id=token.family_id,
    )


def revoke_family_by_raw(session: Session, raw: str, *, clock: Clock) -> int:
    """登出用：以 raw 找到 family 後撤銷整個 family，回撤銷筆數；raw 不存在回 0（登出冪等）。"""
    family_id = session.execute(
        select(RefreshToken.family_id).where(RefreshToken.token_hash == hash_refresh(raw))
    ).scalar_one_or_none()
    if family_id is None:
        return 0
    return _revoke_locked(session, RefreshToken.family_id == family_id, now=clock.now())


def revoke_all_for_subject(
    session: Session, *, subject_type: SubjectType, subject_id: UUID, clock: Clock
) -> int:
    """撤銷該 subject 全部 family（只比對同 subject_type，uuid 相同的另一型不受影響）。"""
    return _revoke_locked(
        session,
        RefreshToken.subject_type == subject_type,
        RefreshToken.subject_id == subject_id,
        now=clock.now(),
    )
