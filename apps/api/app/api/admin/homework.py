"""後台作業進度 endpoint（domain_spec M7）。

- BACKEND-385 ``GET /homework/board``：homework:read；``BoardQuery``（date 預設今天、class_id）→
  BACKEND-383 ``get_board`` → ``BoardOut``。
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
from app.schemas.homework import BoardOut, BoardQuery
from app.services import homework_service

router = APIRouter(prefix="/homework", tags=["admin-homework"])


@router.get("/board", response_model=BoardOut)
def get_board(
    _: Annotated[CurrentStaff, Depends(require_permission(Permission.HOMEWORK_READ))],
    query: Annotated[BoardQuery, Depends(query_model(BoardQuery))],
    db: Annotated[Session, Depends(get_db)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> BoardOut:
    return homework_service.get_board(db, query, clock=clock)
