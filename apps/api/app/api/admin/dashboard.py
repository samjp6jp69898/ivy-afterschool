"""BACKEND-492：後台首頁儀表板 endpoint（domain_spec M11）。

``GET /api/admin/dashboard/today``：dashboard:read；無參數，「今日」以注入的 clock（Asia/Taipei）
判定 → BACKEND-491 ``get_today_dashboard`` → ``DashboardTodayOut``（非營業日
``is_service_day=false``）。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import CurrentStaff, require_permission
from app.core.clock import Clock, get_clock
from app.core.db import get_db
from app.core.permissions import Permission
from app.schemas.dashboard import DashboardTodayOut
from app.services import dashboard_service

router = APIRouter(prefix="/dashboard", tags=["admin-dashboard"])


@router.get("/today", response_model=DashboardTodayOut)
def get_today(
    _: Annotated[CurrentStaff, Depends(require_permission(Permission.DASHBOARD_READ))],
    db: Annotated[Session, Depends(get_db)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> DashboardTodayOut:
    return dashboard_service.get_today_dashboard(db, clock=clock)
