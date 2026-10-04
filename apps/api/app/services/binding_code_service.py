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

BACKEND-055：``claim``（移植 ivy ``api/parent_portal/auth.py::_claim_binding_code_atomic`` /
``_claim_guardian_for_user`` / ``_diagnose_binding_failure``）。

1. 正規化後長度不是 8 或含字母表外字元 → 400 ``binding_code_invalid``。
2. 原子更新 ``used_at = now where code_hash and used_at is null and expires_at > now``；0 列 →
   診斷：查無 → 400 ``binding_code_invalid``；過期 → 400 ``binding_code_expired``（過期優先）；
   否則 400 ``binding_code_used``。
3. guardian 或學生已封存 → 400 ``binding_code_invalid``（不洩漏原因）。
4. 條件式更新 guardian：``parent_account_id is null or = 自己`` 才綁；0 列 → 409
   ``guardian_already_bound``（兩人搶同一 guardian 時後到者失敗）。
5. 在 savepoint 內執行；撞到 ``uq_guardians_student_parent``（同一家長已由另一筆 guardian 綁此
   學生）→ 409 ``already_bound_to_student``。
- 任何錯誤由呼叫端 rollback（碼的 used_at 一併還原）；失敗計數由 BACKEND-056 處理。
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Final, cast
from uuid import UUID

from psycopg.errors import UniqueViolation
from sqlalchemy import CursorResult, delete, or_, select, update
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
_GUARDIAN_STUDENT_PARENT_UNIQUE: Final = "uq_guardians_student_parent"
_ALPHABET_SET: Final = frozenset(CODE_ALPHABET)
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


def _invalid_code() -> AppError:
    return AppError("binding_code_invalid", "綁定碼無效", status=400)


def _is_well_formed(code: str) -> bool:
    return len(code) == CODE_LENGTH and set(code) <= _ALPHABET_SET


def _diagnose_failure(session: Session, code_hash: str, now: datetime) -> AppError:
    row = session.execute(
        select(ParentBindingCode.expires_at, ParentBindingCode.used_at).where(
            ParentBindingCode.code_hash == code_hash
        )
    ).one_or_none()
    if row is None:
        return _invalid_code()
    if row.expires_at <= now:
        return AppError(
            "binding_code_expired", "綁定碼已過期，請向安親班索取新的綁定碼", status=400
        )
    return AppError("binding_code_used", "綁定碼已被使用", status=400)


def _is_student_parent_collision(exc: IntegrityError) -> bool:
    return getattr(exc.orig, "sqlstate", None) == UniqueViolation.sqlstate and (
        _GUARDIAN_STUDENT_PARENT_UNIQUE in str(exc.orig)
    )


def claim(session: Session, *, raw_code: str, parent_account_id: UUID, clock: Clock) -> Guardian:
    code = normalize_code(raw_code)
    if not _is_well_formed(code):
        raise _invalid_code()
    now = clock.now()
    code_hash = hash_code(code)

    guardian_id = session.execute(
        update(ParentBindingCode)
        .where(
            ParentBindingCode.code_hash == code_hash,
            ParentBindingCode.used_at.is_(None),
            ParentBindingCode.expires_at > now,
        )
        .values(used_at=now)
        .returning(ParentBindingCode.guardian_id)
    ).scalar_one_or_none()
    if guardian_id is None:
        raise _diagnose_failure(session, code_hash, now)

    # Guardian.student 為 lazy='joined'，一次帶出
    guardian = session.execute(
        select(Guardian).where(Guardian.id == guardian_id)
    ).scalar_one_or_none()
    if guardian is None or guardian.archived_at is not None or guardian.student.archived_at:
        raise _invalid_code()

    try:
        with session.begin_nested():
            result = session.execute(
                update(Guardian)
                .where(
                    Guardian.id == guardian.id,
                    Guardian.archived_at.is_(None),
                    or_(
                        Guardian.parent_account_id.is_(None),
                        Guardian.parent_account_id == parent_account_id,
                    ),
                )
                .values(parent_account_id=parent_account_id)
            )
    except IntegrityError as exc:
        if _is_student_parent_collision(exc):
            raise ConflictError(
                "already_bound_to_student", "您已綁定此學生，不需重複綁定"
            ) from None
        raise
    if int(cast(CursorResult[Any], result).rowcount or 0) == 0:
        raise ConflictError("guardian_already_bound", "此監護人已由其他家長帳號綁定")
    # bulk update 不經 ORM：重新載入該列（含 parent_account relationship）
    session.refresh(guardian)
    return guardian
