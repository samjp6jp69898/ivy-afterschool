"""BACKEND-102：稽核寫入（audit_logs，與業務同交易）。

``record`` 只 add + flush，不 commit：業務 rollback 時稽核一併回滾（不記錄沒發生的事）。
before / after 轉成 JSON 相容值，並遞迴遮罩敏感 key。action 格式不符是程式錯誤（ValueError），
不是使用者錯誤。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import TYPE_CHECKING, Any, Final, Literal, Self
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.audit import AuditLog

if TYPE_CHECKING:
    from app.api.deps import CurrentParent, CurrentStaff
    from app.core.request_meta import RequestMeta

_ACTION_PATTERN: Final = re.compile(r"^[a-z_]+\.[a-z_]+$")
_SENSITIVE_KEY_PARTS: Final = (
    "password",
    "token",
    "secret",
    "code_hash",
    "id_number",
    "health_note",
)
REDACTED: Final = "***"


@dataclass(frozen=True)
class Actor:
    type: Literal["staff", "parent", "system", "device"]
    id: UUID | None

    @classmethod
    def staff(cls, staff: CurrentStaff) -> Self:
        return cls(type="staff", id=staff.id)

    @classmethod
    def parent(cls, parent: CurrentParent) -> Self:
        return cls(type="parent", id=parent.id)

    @classmethod
    def system(cls) -> Self:
        return cls(type="system", id=None)


def _is_sensitive(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in _SENSITIVE_KEY_PARTS)


def _to_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(k): REDACTED if _is_sensitive(str(k)) else _to_json(v) for k, v in value.items()
        }
    if isinstance(value, list | tuple | set | frozenset):
        return [_to_json(v) for v in value]
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    # UUID、Decimal 與其他未知型別一律字串化
    return str(value)


def record(
    session: Session,
    *,
    actor: Actor,
    action: str,
    entity_type: str,
    entity_id: UUID | str | None,
    before: Mapping[str, Any] | None = None,
    after: Mapping[str, Any] | None = None,
    meta: RequestMeta | None = None,
) -> AuditLog:
    if not _ACTION_PATTERN.fullmatch(action):
        raise ValueError(f"audit action 格式必須為 <entity>.<verb>（小寫與底線）：{action!r}")
    if actor.type != "system" and actor.id is None:
        raise ValueError(f"actor.type={actor.type} 必須帶 actor.id")

    row = AuditLog(
        actor_type=actor.type,
        actor_id=actor.id,
        action=action,
        entity_type=entity_type,
        entity_id=None if entity_id is None else str(entity_id),
        before=None if before is None else _to_json(before),
        after=None if after is None else _to_json(after),
        ip=meta.ip if meta else None,
        user_agent=meta.user_agent if meta else None,
    )
    session.add(row)
    session.flush()
    return row
