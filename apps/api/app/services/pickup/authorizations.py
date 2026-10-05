"""代理接送授權（domain_spec M7）。

- BACKEND-421 / 423：代理接送授權列表（家長端、後台核驗清單）。
- BACKEND-420：``create_authorization``（家長建立單日代理授權，產生接送碼）。
- BACKEND-424：``load_verifiable_authorization``（verify / confirm_visual_match / override 共用的
  鎖定與檢查）。
- BACKEND-523：``regenerate_code``（家長重新產生接送碼：舊碼立即失效、重設連錯與鎖定、寫 audit）。
- BACKEND-425：``complete_via_authorization``（核銷後完成授權與接送請求、出勤改 left、通知家長）。
  鎖序：授權列（BACKEND-424 已鎖）→ 請求列 → 出勤列。

列表只回 ``code_last4``，不回 ``code_hash``（單向 HMAC）；明碼只在建立時的回應出現一次，DB 與 log
都不保存。``effective_status``：active 且 service_date 早於今天（台北）→ ``expired``，其餘同
status。
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.api.deps import CurrentParent, CurrentStaff
from app.core.clock import Clock, to_taipei
from app.core.errors import AppError, ConflictError, NotFoundError
from app.core.request_meta import RequestMeta
from app.core.settings_registry import PICKUP_AUTHORIZATION
from app.core.storage import Storage
from app.models.account import StaffUser
from app.models.homework import HomeworkDailyProgress
from app.models.pickup import OPEN_STATUSES, PickupAuthorization, PickupPerson, PickupRequest
from app.models.students import Student
from app.notifications.events import Event
from app.notifications.recipients import parent_recipients
from app.notifications.service import enqueue
from app.realtime.publish import broadcast_after_commit
from app.repositories.students import get_student_or_404, student_brief_map
from app.schemas.pickup import (
    AuthorizationCompleteOut,
    PickupAuthorizationCreatedOut,
    PickupAuthorizationCreateIn,
    PickupAuthorizationOut,
    PickupStudentOut,
    StaffAuthorizationListQuery,
    StaffAuthorizationOut,
)
from app.services.attendance_service import mark_left_by_pickup
from app.services.audit_service import Actor, record
from app.services.parent_scope import get_parent_student_ids
from app.services.pickup.codes import generate_pickup_code, hash_pickup_code, pickup_code_last4
from app.services.pickup.persons import signed_photo_url
from app.services.pickup.transitions import transition_request
from app.services.pickup.views import build_request_views, publish_request_change
from app.services.settings_service import get_setting

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


def create_authorization(
    session: Session,
    student_id: UUID,
    data: PickupAuthorizationCreateIn,
    *,
    parent: CurrentParent,
    clock: Clock,
) -> PickupAuthorizationCreatedOut:
    """呼叫端已以 ``get_owned_student_for_write`` 驗證所有權。

    移植 ivy ``api/parent_portal/pickup.py::create_authorizations`` 的日期範圍與接送人快照；去掉一次
    多孩、batch_key、可逆加密、連動建立接送請求。先鎖學生列再計數，並發建立時上限仍成立。
    """
    settings = get_setting(session, PICKUP_AUTHORIZATION)
    today = clock.today()
    if not today <= data.service_date <= today + timedelta(days=settings.max_days_ahead):
        raise AppError(
            "invalid_service_date",
            f"代理接送日期必須在今天起 {settings.max_days_ahead} 天內",
            status=422,
            details={"max_days_ahead": settings.max_days_ahead},
        )

    if data.pickup_person_id is not None:
        person = session.execute(
            select(PickupPerson).where(
                PickupPerson.id == data.pickup_person_id,
                PickupPerson.student_id == student_id,
                PickupPerson.archived_at.is_(None),
            )
        ).scalar_one_or_none()
        if person is None:
            raise NotFoundError("pickup_person_not_found", "找不到接送人")
        proxy_name, proxy_phone = person.name, person.phone
    else:
        if data.proxy_name is None or data.proxy_phone is None:
            # schema 已保證臨時代理模式兩者皆有值；走到這裡是程式錯誤
            raise ValueError("臨時代理模式必須提供 proxy_name 與 proxy_phone")
        proxy_name, proxy_phone = data.proxy_name, data.proxy_phone

    # 同一學生的建立在學生列上序列化：計數與寫入之間不會被另一個交易插入
    get_student_or_404(session, student_id, include_archived=True, for_update=True)
    active_count = session.execute(
        select(func.count())
        .select_from(PickupAuthorization)
        .where(
            PickupAuthorization.student_id == student_id,
            PickupAuthorization.service_date == data.service_date,
            PickupAuthorization.status == "active",
        )
    ).scalar_one()
    if active_count >= settings.max_active_per_day:
        raise ConflictError(
            "authorization_limit_reached",
            f"同一天最多建立 {settings.max_active_per_day} 筆代理接送授權",
            details={"max_active_per_day": settings.max_active_per_day},
        )

    code = generate_pickup_code()
    auth = PickupAuthorization(
        student_id=student_id,
        service_date=data.service_date,
        pickup_person_id=data.pickup_person_id,
        proxy_name=proxy_name,
        proxy_phone=proxy_phone,
        code_hash=hash_pickup_code(code),
        code_last4=pickup_code_last4(code),
        status="active",
        created_by_parent_id=parent.id,
    )
    session.add(auth)
    session.flush()

    out = PickupAuthorizationOut(**_base_fields(auth, today))
    broadcast_after_commit(
        session,
        topic="pickup",
        type="pickup.authorization_updated",
        data=out.model_dump(),
        clock=clock,
    )
    return PickupAuthorizationCreatedOut(authorization=out, code=code)


def load_verifiable_authorization(
    session: Session, auth_id: UUID, *, clock: Clock, allow_locked: bool = False
) -> PickupAuthorization:
    """鎖定（FOR UPDATE）並檢查可核銷；鎖定後不自動解鎖，只有 override 以 allow_locked 處理。

    移植 ivy ``services/pickup_verification.py::_load_active``；ivy 的 403 collapse 改為 404 / 409
    分開（後台員工本來就看得到全部授權清單，不需防列舉）。
    """
    auth = session.execute(
        select(PickupAuthorization)
        .where(PickupAuthorization.id == auth_id)
        # pickup_person 是 joined（outer join 的可空側不能鎖），只鎖授權列；populate_existing 以
        # 上鎖後的 DB 現值覆蓋同 session 內已載入的舊屬性
        .with_for_update(of=PickupAuthorization)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if auth is None:
        raise NotFoundError("pickup_authorization_not_found", "找不到代理接送授權")
    if auth.status != "active":
        raise ConflictError(
            "authorization_not_active",
            "此代理接送授權已完成或已取消",
            details={"status": auth.status},
        )
    if auth.service_date != clock.today():
        raise ConflictError("authorization_not_today", "此代理接送授權不是今天的")
    if auth.code_locked_at is not None and not allow_locked:
        raise ConflictError("pickup_code_locked", "接送碼錯誤次數過多已鎖定，請由老師確認後處理")
    return auth


def regenerate_code(
    session: Session,
    auth_id: UUID,
    *,
    parent: CurrentParent,
    meta: RequestMeta,
    clock: Clock,
) -> PickupAuthorizationCreatedOut:
    """家長遺失接送碼時換新碼：舊碼立即失效，連錯次數與鎖定一併重設；新碼只在此回應出現一次。

    先 FOR UPDATE 鎖授權列，與員工核銷序列化（已被核銷者在此得到 409）。他人小孩的授權與不存在
    回同一個 404。移植 ivy ``api/parent_portal/pickup.py::regenerate_code``；去掉同批次多孩共碼與
    可逆加密。
    """
    auth = session.execute(
        select(PickupAuthorization)
        .where(PickupAuthorization.id == auth_id)
        .with_for_update(of=PickupAuthorization)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if auth is None or auth.student_id not in get_parent_student_ids(session, parent.id):
        raise NotFoundError("pickup_authorization_not_found", "找不到代理接送授權")
    student_status = session.execute(
        select(Student.status).where(Student.id == auth.student_id)
    ).scalar_one()
    if student_status == "withdrawn":
        raise AppError("student_not_active", "此學生已退班，無法進行此操作", status=409)
    if auth.status != "active":
        raise ConflictError("authorization_not_active", "此代理接送授權已完成或已取消")
    if auth.service_date < clock.today():
        raise ConflictError("authorization_expired", "此代理接送授權已過期")

    before = {
        "code_last4": auth.code_last4,
        "code_attempts": auth.code_attempts,
        "locked": auth.code_locked_at is not None,
    }
    code = generate_pickup_code()
    hit = session.execute(
        update(PickupAuthorization)
        .where(PickupAuthorization.id == auth_id, PickupAuthorization.status == "active")
        .values(
            code_hash=hash_pickup_code(code),
            code_last4=pickup_code_last4(code),
            code_attempts=0,
            code_locked_at=None,
        )
        .returning(PickupAuthorization.id)
    ).scalar_one_or_none()
    if hit is None:
        raise ConflictError("authorization_not_active", "此代理接送授權已完成或已取消")
    # bulk update 不經 ORM：以 DB 現值刷新
    session.refresh(auth)

    # 稽核不含明碼與雜湊
    record(
        session,
        actor=Actor.parent(parent),
        action="pickup_authorization.regenerate_code",
        entity_type="pickup_authorization",
        entity_id=auth.id,
        before=before,
        after={"code_last4": auth.code_last4, "code_attempts": 0, "locked": False},
        meta=meta,
    )
    out = PickupAuthorizationOut(**_base_fields(auth, clock.today()))
    broadcast_after_commit(
        session,
        topic="pickup",
        type="pickup.authorization_updated",
        data=out.model_dump(),
        clock=clock,
    )
    return PickupAuthorizationCreatedOut(authorization=out, code=code)


def complete_via_authorization(
    session: Session,
    auth: PickupAuthorization,
    method: Literal["code", "visual_match", "override"],
    *,
    actor: CurrentStaff,
    clock: Clock,
) -> AuthorizationCompleteOut:
    """verify / confirm_visual_match / override 共用的核銷收尾（auth 已由 BACKEND-424 鎖定並檢查）。

    該生該日有進行中的接送請求 → 一併完成；沒有 → 新增一筆 source='proxy' 的已完成請求，讓接送紀錄
    與佇列歷史完整。移植 ivy ``services/pickup_verification.py::_complete`` /
    ``_close_linked_call``。
    """
    now = clock.now()
    auth.status = "completed"
    auth.verified_at = now
    auth.verified_by = actor.id
    auth.verification_method = method
    session.flush()

    completion: dict[str, Any] = {
        "completed_at": now,
        "completed_by": actor.id,
        "completion_method": method,
        "picked_up_by_authorization_id": auth.id,
    }
    open_id = session.execute(
        select(PickupRequest.id).where(
            PickupRequest.student_id == auth.student_id,
            PickupRequest.service_date == auth.service_date,
            PickupRequest.status.in_(OPEN_STATUSES),
        )
    ).scalar_one_or_none()
    if open_id is not None:
        request = transition_request(
            session,
            open_id,
            from_statuses=OPEN_STATUSES,
            to_status="completed",
            values={**completion, "arrived_at": func.coalesce(PickupRequest.arrived_at, now)},
        )
    else:
        homework = session.execute(
            select(HomeworkDailyProgress.overall_status).where(
                HomeworkDailyProgress.student_id == auth.student_id,
                HomeworkDailyProgress.service_date == auth.service_date,
            )
        ).scalar_one_or_none()
        # 直接為終態：不會撞到 uq_pickup_requests_one_open
        request = PickupRequest(
            student_id=auth.student_id,
            service_date=auth.service_date,
            source="proxy",
            requested_by_type="staff",
            requested_by_id=actor.id,
            status="completed",
            homework_status_at_request=homework or "not_started",
            arrived_at=now,
            **completion,
        )
        session.add(request)
        session.flush()

    mark_left_by_pickup(session, auth.student_id, auth.service_date, at=now, clock=clock)
    student_name = session.execute(
        select(Student.name).where(Student.id == auth.student_id)
    ).scalar_one()
    enqueue(
        session,
        Event.PICKUP_COMPLETED,
        recipients=parent_recipients(session, auth.student_id),
        payload={
            "student_id": auth.student_id,
            "student_name": student_name,
            "request_id": request.id,
            "time": to_taipei(now).strftime("%H:%M"),
            "picked_up_by": f"代理人 {auth.proxy_name}",
        },
        clock=clock,
    )
    publish_request_change(session, request, clock=clock)

    today = clock.today()
    brief = student_brief_map(session, [auth.student_id])[auth.student_id]
    authorization = StaffAuthorizationOut(
        **_base_fields(auth, today),
        student=PickupStudentOut(
            id=brief.id,
            student_no=brief.student_no,
            name=brief.name,
            grade_level=brief.grade_level,
            class_id=brief.class_id,
            class_name=brief.class_name,
        ),
        # 沒有 storage 可簽照片網址：核銷結果不需要照片，前端要時重抓核驗清單
        photo_url=None,
        code_attempts=auth.code_attempts,
        locked=auth.code_locked_at is not None,
        verified_by_name=actor.display_name,
    )
    broadcast_after_commit(
        session,
        topic="pickup",
        type="pickup.authorization_updated",
        data=PickupAuthorizationOut(**_base_fields(auth, today)).model_dump(),
        clock=clock,
    )
    [request_out] = build_request_views(session, [request])
    return AuthorizationCompleteOut(authorization=authorization, request=request_out)
