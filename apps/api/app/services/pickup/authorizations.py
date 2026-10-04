"""BACKEND-421 / 423：代理接送授權列表（家長端、後台核驗清單）。

兩者都只回 ``code_last4``，不回 ``code_hash``（單向 HMAC，明碼只在建立時回傳一次）。
``effective_status``：active 且 service_date 早於今天（台北）→ ``expired``，其餘同 status。
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import Clock
from app.core.storage import Storage
from app.models.account import StaffUser
from app.models.pickup import PickupAuthorization
from app.repositories.students import student_brief_map
from app.schemas.pickup import (
    PickupAuthorizationOut,
    PickupStudentOut,
    StaffAuthorizationListQuery,
    StaffAuthorizationOut,
)
from app.services.pickup.persons import signed_photo_url

CHILD_LIST_WINDOW_DAYS = 30


def _base_fields(auth: PickupAuthorization, today: date) -> dict[str, Any]:
    expired = auth.status == "active" and auth.service_date < today
    return {
        "id": auth.id,
        "student_id": auth.student_id,
        "service_date": auth.service_date,
        "pickup_person_id": auth.pickup_person_id,
        "proxy_name": auth.proxy_name,
        "proxy_phone": auth.proxy_phone,
        "code_last4": auth.code_last4,
        "status": auth.status,
        "effective_status": "expired" if expired else auth.status,
        "verified_at": auth.verified_at,
        "verification_method": auth.verification_method,
        "created_at": auth.created_at,
    }


def list_child_authorizations(
    session: Session, student_id: UUID, *, clock: Clock
) -> list[PickupAuthorizationOut]:
    """呼叫端已驗證所有權；service_date ≥ 今天 - 30，service_date desc、created_at desc。"""
    today = clock.today()
    auths = session.execute(
        select(PickupAuthorization)
        .where(
            PickupAuthorization.student_id == student_id,
            PickupAuthorization.service_date >= today - timedelta(days=CHILD_LIST_WINDOW_DAYS),
        )
        .order_by(
            PickupAuthorization.service_date.desc(),
            PickupAuthorization.created_at.desc(),
            PickupAuthorization.id,
        )
    ).scalars()
    return [PickupAuthorizationOut(**_base_fields(auth, today)) for auth in auths]


def list_authorizations_for_staff(
    session: Session,
    query: StaffAuthorizationListQuery,
    *,
    storage: Storage,
    clock: Clock,
) -> list[StaffAuthorizationOut]:
    """後台代理接送核驗清單：指定日期（預設今天）、可依狀態篩選；active 在前，再依學生姓名。

    學生與員工姓名各以一次查詢取得（不 N+1）；接送人在 ``PickupAuthorization`` 以 joined 載入。
    """
    today = clock.today()
    stmt = select(PickupAuthorization).where(
        PickupAuthorization.service_date == (query.date or today)
    )
    if query.status is not None:
        stmt = stmt.where(PickupAuthorization.status == query.status)
    auths = list(session.execute(stmt).scalars().unique())

    students = student_brief_map(session, {auth.student_id for auth in auths})
    verifier_ids = {auth.verified_by for auth in auths if auth.verified_by is not None}
    verifier_names = (
        {
            row.id: row.display_name
            for row in session.execute(
                select(StaffUser.id, StaffUser.display_name).where(StaffUser.id.in_(verifier_ids))
            )
        }
        if verifier_ids
        else {}
    )

    auths.sort(
        key=lambda a: (
            a.status != "active",
            students[a.student_id].name,
            a.created_at,
            a.id,
        )
    )
    rows = []
    for auth in auths:
        brief = students[auth.student_id]
        rows.append(
            StaffAuthorizationOut(
                **_base_fields(auth, today),
                student=PickupStudentOut(
                    id=brief.id,
                    student_no=brief.student_no,
                    name=brief.name,
                    grade_level=brief.grade_level,
                    class_id=brief.class_id,
                    class_name=brief.class_name,
                ),
                photo_url=(
                    signed_photo_url(storage, auth.pickup_person)
                    if auth.pickup_person is not None
                    else None
                ),
                code_attempts=auth.code_attempts,
                locked=auth.code_locked_at is not None,
                verified_by_name=verifier_names.get(auth.verified_by) if auth.verified_by else None,
            )
        )
    return rows
