"""BACKEND-405：接送請求條件式狀態轉換（回覆、確認、抵達、完成、取消、過期都經過它）。

移植 ivy ``api/portal/dismissal_calls.py::_db_transition_call`` 的「合法前置狀態清單」概念；ivy 的
``with_for_update`` 後檢查改為**單一條件式 UPDATE**（``WHERE id = :id AND status = ANY(:from)``），
「不存在與越權 collapse」改為 404。

- 兩位員工同時按「完成」、家長取消與員工完成同時發生、過期工作與完成同時發生：只有一個 UPDATE 命中
  WHERE 的 status 條件，另一個得到 409，不會重複寫入 completed_by、重複通知或重複改出勤。
- 0 列 → 再查 ``id, status, student_id``：不存在、或 ``student_ids`` 給了而不在範圍 → 404
  ``pickup_request_not_found``（家長 IDOR 不洩漏）；存在 → 409 ``invalid_pickup_status``，details
  ``{"current_status": ...}``。
- ``values`` 可含 SQL 表達式（例如 ``arrived_at = coalesce(arrived_at, :now)``）；``to_status=None``
  時只改欄位。回傳以 ``populate_existing`` 重新載入的 ORM 物件。只 flush 不 commit。
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError
from app.models.pickup import PickupRequest

__all__ = ["transition_request"]


def _not_found() -> NotFoundError:
    return NotFoundError("pickup_request_not_found", "找不到接送請求")


def transition_request(
    session: Session,
    request_id: UUID,
    *,
    from_statuses: Collection[str],
    to_status: str | None,
    values: Mapping[str, Any],
    student_ids: Collection[UUID] | None = None,
) -> PickupRequest:
    assigned: dict[str, Any] = dict(values)
    if to_status is not None:
        assigned["status"] = to_status
    if not assigned:
        # 沒有要改的欄位：以 no-op 指派維持單一 UPDATE 語意（仍檢查狀態 / 範圍）
        assigned["status"] = PickupRequest.status

    stmt = (
        update(PickupRequest)
        .where(PickupRequest.id == request_id, PickupRequest.status.in_(list(from_statuses)))
        .values(**assigned)
        .returning(PickupRequest.id)
    )
    if student_ids is not None:
        stmt = stmt.where(PickupRequest.student_id.in_(list(student_ids)))
    hit = session.execute(stmt).scalar_one_or_none()

    if hit is None:
        current = session.execute(
            select(PickupRequest.status, PickupRequest.student_id).where(
                PickupRequest.id == request_id
            )
        ).one_or_none()
        if current is None or (
            student_ids is not None and current.student_id not in set(student_ids)
        ):
            raise _not_found()
        raise ConflictError(
            "invalid_pickup_status",
            "接送請求狀態已變更，無法執行此操作",
            details={"current_status": current.status},
        )

    # bulk update 不經 ORM：以 DB 現值覆蓋同 session 內可能已載入的舊屬性
    return session.execute(
        select(PickupRequest)
        .where(PickupRequest.id == request_id)
        .execution_options(populate_existing=True)
    ).scalar_one()
