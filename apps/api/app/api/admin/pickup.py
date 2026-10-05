"""後台接送 endpoint（domain_spec M8）。

- BACKEND-429 ``GET /pickup/queue``：pickup:read；``PickupQueueQuery``（date 預設今天）→ BACKEND-414
  → ``PickupQueueOut``（open / closed / counts）。
- BACKEND-435 ``GET /pickup/roster``：pickup:read；``RosterQuery`` → BACKEND-415 → ``RosterOut``
  （POS 學生卡，依班級分組）。
- BACKEND-436 ``GET /pickup/authorizations``：pickup:read；``StaffAuthorizationListQuery`` +
  storage → BACKEND-423 → ``list[StaffAuthorizationOut]``（代理接送核驗清單，只含 code_last4）。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.admin._query import query_model
from app.api.deps import CurrentStaff, require_permission
from app.core.clock import Clock, get_clock
from app.core.db import get_db
from app.core.permissions import Permission
from app.core.storage import Storage, get_storage
from app.schemas.pickup import (
    PickupQueueOut,
    PickupQueueQuery,
    RosterOut,
    RosterQuery,
    StaffAuthorizationListQuery,
    StaffAuthorizationOut,
)
from app.services.pickup import authorizations as authorization_service
from app.services.pickup import requests as request_service

router = APIRouter(prefix="/pickup", tags=["admin-pickup"])

PickupRead = Annotated[CurrentStaff, Depends(require_permission(Permission.PICKUP_READ))]
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
