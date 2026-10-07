"""後台接送 endpoint（domain_spec M8）。

- BACKEND-429 ``GET /pickup/queue``：pickup:read；``PickupQueueQuery``（date 預設今天）→ BACKEND-414
  → ``PickupQueueOut``（open / closed / counts）。
- BACKEND-435 ``GET /pickup/roster``：pickup:read；``RosterQuery`` → BACKEND-415 → ``RosterOut``
  （POS 學生卡，依班級分組）。
- BACKEND-436 ``GET /pickup/authorizations``：pickup:read；``StaffAuthorizationListQuery`` +
  storage → BACKEND-423 → ``list[StaffAuthorizationOut]``（代理接送核驗清單，只含 code_last4）。
- BACKEND-430 ``POST /pickup/requests``：pickup:operate；``StaffPickupRequestCreateIn`` →
  BACKEND-406 ``create_request``（actor = ``Actor.staff``、source=staff；同日已有進行中 409
  ``pickup_request_exists``、請假 / 缺席 / 已離班 409 ``student_not_available``）→ commit → 201。
- BACKEND-431 ``POST /pickup/requests/{request_id}/reply``：pickup:operate；``PickupReplyIn`` →
  BACKEND-407 ``reply_request``（reply_source=staff，ETA 同交易寫回作業進度；終態 409
  ``invalid_pickup_status``）→ commit → ``PickupRequestOut``。
- BACKEND-432 ``POST /pickup/requests/{request_id}/acknowledge``：pickup:operate；無 body →
  BACKEND-408（非 pending 409 ``invalid_pickup_status``）→ commit → ``PickupRequestOut``。
- BACKEND-434 ``POST /pickup/requests/{request_id}/cancel``：pickup:operate；``PickupCancelIn`` 可
  省略 → BACKEND-411（終態 409；家長發起者通知該家長）→ commit → ``PickupRequestOut``。
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.admin._query import query_model
from app.api.deps import CurrentStaff, require_permission
from app.core.clock import Clock, get_clock
from app.core.db import get_db
from app.core.permissions import Permission
from app.core.storage import Storage, get_storage
from app.schemas.pickup import (
    PickupCancelIn,
    PickupQueueOut,
    PickupQueueQuery,
    PickupReplyIn,
    PickupRequestOut,
    RosterOut,
    RosterQuery,
    StaffAuthorizationListQuery,
    StaffAuthorizationOut,
    StaffPickupRequestCreateIn,
)
from app.services.audit_service import Actor
from app.services.pickup import authorizations as authorization_service
from app.services.pickup import requests as request_service
from app.services.pickup.views import build_request_views

router = APIRouter(prefix="/pickup", tags=["admin-pickup"])

PickupRead = Annotated[CurrentStaff, Depends(require_permission(Permission.PICKUP_READ))]
PickupOperate = Annotated[CurrentStaff, Depends(require_permission(Permission.PICKUP_OPERATE))]
Db = Annotated[Session, Depends(get_db)]
ClockDep = Annotated[Clock, Depends(get_clock)]


@router.get("/queue", response_model=PickupQueueOut)
def get_queue(
    _: PickupRead,
    query: Annotated[PickupQueueQuery, Depends(query_model(PickupQueueQuery))],
    db: Db,
    clock: ClockDep,
) -> PickupQueueOut:
    return request_service.get_queue(db, query, clock=clock)


@router.get("/roster", response_model=RosterOut)
def get_roster(
    _: PickupRead,
    query: Annotated[RosterQuery, Depends(query_model(RosterQuery))],
    db: Db,
    clock: ClockDep,
) -> RosterOut:
    return request_service.get_roster(db, query, clock=clock)


@router.get("/authorizations", response_model=list[StaffAuthorizationOut])
def list_authorizations(
    _: PickupRead,
    query: Annotated[
        StaffAuthorizationListQuery, Depends(query_model(StaffAuthorizationListQuery))
    ],
    db: Db,
    clock: ClockDep,
    storage: Annotated[Storage, Depends(get_storage)],
) -> list[StaffAuthorizationOut]:
    return authorization_service.list_authorizations_for_staff(
        db, query, storage=storage, clock=clock
    )


@router.post("/requests", status_code=201, response_model=PickupRequestOut)
def create_request(
    body: StaffPickupRequestCreateIn, staff: PickupOperate, db: Db, clock: ClockDep
) -> PickupRequestOut:
    request = request_service.create_request(db, body, actor=Actor.staff(staff), clock=clock)
    out = build_request_views(db, [request])[0]
    db.commit()
    return out


@router.post("/requests/{request_id}/reply", response_model=PickupRequestOut)
def reply_request(
    request_id: UUID, body: PickupReplyIn, staff: PickupOperate, db: Db, clock: ClockDep
) -> PickupRequestOut:
    request = request_service.reply_request(db, request_id, body, actor=staff, clock=clock)
    out = build_request_views(db, [request])[0]
    db.commit()
    return out


@router.post("/requests/{request_id}/acknowledge", response_model=PickupRequestOut)
def acknowledge_request(
    request_id: UUID, staff: PickupOperate, db: Db, clock: ClockDep
) -> PickupRequestOut:
    request = request_service.acknowledge_request(db, request_id, actor=staff, clock=clock)
    out = build_request_views(db, [request])[0]
    db.commit()
    return out


@router.post("/requests/{request_id}/cancel", response_model=PickupRequestOut)
def cancel_request(
    request_id: UUID,
    staff: PickupOperate,
    db: Db,
    clock: ClockDep,
    body: PickupCancelIn | None = None,
) -> PickupRequestOut:
    request = request_service.cancel_request(
        db, request_id, body, actor=Actor.staff(staff), clock=clock
    )
    out = build_request_views(db, [request])[0]
    db.commit()
    return out
