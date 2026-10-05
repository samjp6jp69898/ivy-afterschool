"""BACKEND-102：稽核寫入（audit_logs，與業務同交易）。

``record`` 只 add + flush，不 commit：業務 rollback 時稽核一併回滾（不記錄沒發生的事）。
before / after 轉成 JSON 相容值，並遞迴遮罩敏感 key（BACKEND-547：must_change_password、
token_version 這類狀態旗標以精確比對白名單放行）。action 格式不符是程式錯誤（ValueError），
不是使用者錯誤。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import TYPE_CHECKING, Any, Final, Literal, Self
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import taipei_day_bounds
from app.core.pagination import Page, PageParams, paginate
from app.models.account import StaffUser
from app.models.audit import AuditLog
from app.models.parents import ParentAccount
from app.schemas.audit import AuditLogOut, AuditLogQuery

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
# 含敏感片段但不是機密的狀態欄位：整個 key（不分大小寫）完全相等才放行
_NON_SENSITIVE_KEYS: Final = frozenset({"must_change_password", "token_version"})


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
    if lowered in _NON_SENSITIVE_KEYS:
        return False
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


SYSTEM_ACTOR_NAME: Final = "系統"


def list_audit_logs(session: Session, query: AuditLogQuery, page: PageParams) -> Page[AuditLogOut]:
    """篩選 + 分頁，依 created_at、id 新到舊。date_from / date_to 為台北日期，含頭含尾。"""
    stmt = select(AuditLog)
    if query.action is not None:
        stmt = stmt.where(AuditLog.action == query.action)
    if query.action_prefix is not None:
        # autoescape：prefix 內的 _ 與 % 當字面字元（action 本身就含底線）
        stmt = stmt.where(AuditLog.action.startswith(query.action_prefix, autoescape=True))
    if query.entity_type is not None:
        stmt = stmt.where(AuditLog.entity_type == query.entity_type)
    if query.entity_id is not None:
        stmt = stmt.where(AuditLog.entity_id == query.entity_id)
    if query.actor_type is not None:
        stmt = stmt.where(AuditLog.actor_type == query.actor_type)
    if query.actor_id is not None:
        stmt = stmt.where(AuditLog.actor_id == query.actor_id)
    if query.date_from is not None:
        stmt = stmt.where(AuditLog.created_at >= taipei_day_bounds(query.date_from)[0])
    if query.date_to is not None:
        stmt = stmt.where(AuditLog.created_at < taipei_day_bounds(query.date_to)[1])

    rows, total = paginate(
        session, stmt.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()), page
    )
    names = _actor_names(session, rows)
    items = []
    for row in rows:
        items.append(
            AuditLogOut(
                id=row.id,
                created_at=row.created_at,
                actor_type=row.actor_type,
                actor_id=row.actor_id,
                actor_name=_actor_name(row, names),
                action=row.action,
                entity_type=row.entity_type,
                entity_id=row.entity_id,
                before=row.before,
                after=row.after,
                ip=row.ip,
                user_agent=row.user_agent,
            )
        )
    return Page(items=items, total=total)


def _actor_names(session: Session, rows: list[AuditLog]) -> dict[tuple[str, UUID], str | None]:
    """本頁出現的 actor 各以一次 IN 查詢取名稱（不 N+1）。"""
    names: dict[tuple[str, UUID], str | None] = {}
    staff_ids = {r.actor_id for r in rows if r.actor_type == "staff" and r.actor_id}
    parent_ids = {r.actor_id for r in rows if r.actor_type == "parent" and r.actor_id}
    if staff_ids:
        for sid, staff_name in session.execute(
            select(StaffUser.id, StaffUser.display_name).where(StaffUser.id.in_(staff_ids))
        ):
            names[("staff", sid)] = staff_name
    if parent_ids:
        for pid, parent_name in session.execute(
            select(ParentAccount.id, ParentAccount.display_name).where(
                ParentAccount.id.in_(parent_ids)
            )
        ):
            names[("parent", pid)] = parent_name
    return names


def _actor_name(row: AuditLog, names: dict[tuple[str, UUID], str | None]) -> str | None:
    if row.actor_type == "system":
        return SYSTEM_ACTOR_NAME
    if row.actor_id is None:
        return None
    return names.get((row.actor_type, row.actor_id))
