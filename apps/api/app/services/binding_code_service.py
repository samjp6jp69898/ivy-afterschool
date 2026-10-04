"""BACKEND-054：家長綁定碼產生（domain_spec M3 ``parent_binding_codes``，DB-017）。

移植 ivy ``api/guardians_admin.py::create_binding_code`` / ``_generate_plain_code``：保留去除易混淆
字元（0 O 1 I）的字母表、明碼只回傳一次、稽核；改為 8 碼、HMAC（BACKEND-010 ``keyed_hash``）、7 天
效期、產新碼時作廢該監護人所有未使用的舊碼（取代 ivy 的 active 上限 3）。

- guardian 不存在或已封存 → 404 ``guardian_not_found``；學生已封存 → 409 ``student_archived``；
  已綁定家長（``parent_account_id`` 非 null）→ 409 ``guardian_already_bound``（先解除綁定
  BACKEND-172）。
- 先以 ``for update`` 鎖住 guardian 列再作廢 / 新增：同一 guardian 的併發 generate 序列化，不會留下
  兩筆有效碼。
- ``code_hash`` unique 衝突以 savepoint 重試最多 3 次，仍衝突 → 500 ``binding_code_collision``
  （32^8 的空間下實務上不會發生）。
- 稽核 ``guardian.binding_code_issue``：after 只有 ``expires_at``，不含明碼與 hash。
- 正規化（去空白、去連字號、轉大寫）集中在 ``normalize_code``，bind 端（BACKEND-055）必須用同一個
  函式再 ``hash_code``。只 flush 不 commit。
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Final
from uuid import UUID

from psycopg.errors import UniqueViolation
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.clock import Clock
from app.core.crypto import LABEL_HMAC_BINDING_CODE, keyed_hash
from app.core.errors import AppError, ConflictError, NotFoundError
from app.core.request_meta import RequestMeta
from app.models.parents import Guardian, ParentBindingCode
from app.services import audit_service

if TYPE_CHECKING:
    from app.api.deps import CurrentStaff

CODE_ALPHABET: Final = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LENGTH: Final = 8
CODE_TTL: Final = timedelta(days=7)
_MAX_COLLISION_RETRIES: Final = 3
_CODE_HASH_UNIQUE: Final = "uq_parent_binding_codes_code_hash"
_STRIP_CHARS: Final = str.maketrans("", "", " -\t\r\n")


@dataclass(frozen=True)
class IssuedBindingCode:
    guardian_id: UUID
    code: str  # 8 碼明碼，只在此回傳
    expires_at: datetime


def normalize_code(raw: str) -> str:
    """去空白與 ``-``、轉大寫；產碼與兌換都經此函式，同一碼永遠得到同一個 hash。"""
    return raw.translate(_STRIP_CHARS).upper()


def hash_code(normalized: str) -> str:
    return keyed_hash(LABEL_HMAC_BINDING_CODE, normalized)


def _random_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


def _load_guardian_for_issue(session: Session, guardian_id: UUID) -> Guardian:
    # 鎖住 guardian 列：同一 guardian 的併發 generate 序列化，後到者等前者 commit 後才 delete 舊碼，
    # 才刪得掉前者剛寫入的碼（student / parent_account 是 lazy='joined'，of= 避開 outer join）
    guardian = session.execute(
        select(Guardian)
        .where(Guardian.id == guardian_id, Guardian.archived_at.is_(None))
        .with_for_update(of=Guardian)
    ).scalar_one_or_none()
    if guardian is None:
        raise NotFoundError("guardian_not_found", "找不到監護人")
    if guardian.student.archived_at is not None:
        raise ConflictError("student_archived", "學生已封存，無法產生綁定碼")
    if guardian.parent_account_id is not None:
        raise ConflictError("guardian_already_bound", "此監護人已綁定家長帳號，請先解除綁定")
    return guardian


def _is_code_hash_collision(exc: IntegrityError) -> bool:
    return getattr(exc.orig, "sqlstate", None) == UniqueViolation.sqlstate and (
        _CODE_HASH_UNIQUE in str(exc.orig)
    )


def _insert_unique_code(
    session: Session, *, guardian_id: UUID, now: datetime, expires_at: datetime, created_by: UUID
) -> str:
    """抽碼寫入；code_hash unique 衝突時回滾 savepoint 重抽（最多 3 次），其他錯誤照拋。"""
    for _ in range(_MAX_COLLISION_RETRIES):
        code = _random_code()
        try:
            with session.begin_nested():
                session.add(
                    ParentBindingCode(
                        # created_at 取注入時鐘：DB CHECK expires_at > created_at 用同一個「現在」
                        created_at=now,
                        guardian_id=guardian_id,
                        code_hash=hash_code(code),
                        expires_at=expires_at,
                        created_by=created_by,
                    )
                )
                session.flush()
        except IntegrityError as exc:
            if not _is_code_hash_collision(exc):
                raise
            continue
        return code
    raise AppError("binding_code_collision", "綁定碼產生失敗，請再試一次", status=500)


def generate(
    session: Session,
    *,
    guardian_id: UUID,
    actor: CurrentStaff,
    meta: RequestMeta,
    clock: Clock,
) -> IssuedBindingCode:
    guardian = _load_guardian_for_issue(session, guardian_id)
    now = clock.now()
    expires_at = now + CODE_TTL

    # 作廢舊碼：刪掉所有未使用的；已使用的保留（綁定歷史）
    session.execute(
        delete(ParentBindingCode).where(
            ParentBindingCode.guardian_id == guardian.id, ParentBindingCode.used_at.is_(None)
        )
    )
    code = _insert_unique_code(
        session, guardian_id=guardian.id, now=now, expires_at=expires_at, created_by=actor.id
    )
    audit_service.record(
        session,
        actor=audit_service.Actor.staff(actor),
        action="guardian.binding_code_issue",
        entity_type="guardian",
        entity_id=guardian.id,
        after={"expires_at": expires_at},
        meta=meta,
    )
    return IssuedBindingCode(guardian_id=guardian.id, code=code, expires_at=expires_at)
