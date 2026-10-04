"""BACKEND-064：家長端個人資料 endpoint。

``GET /api/parent/me``：家長資料與已綁定小孩清單（只含有效綁定）。供 liff-login、bind、refresh
的回應共用同一個 ``ParentAccountService.get_me``。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import CurrentParent, get_current_parent
from app.core.clock import Clock, get_clock
from app.core.db import get_db
from app.core.storage import Storage, get_storage
from app.schemas.parent_children import ParentMeOut
from app.services import parent_account_service

router = APIRouter(tags=["parent-me"])


@router.get("/me", response_model=ParentMeOut)
def get_me(
    parent: Annotated[CurrentParent, Depends(get_current_parent)],
    db: Annotated[Session, Depends(get_db)],
    storage: Annotated[Storage, Depends(get_storage)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> ParentMeOut:
    return parent_account_service.get_me(db, parent=parent, storage=storage, clock=clock)
