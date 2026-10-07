"""家長端接送 endpoint（domain_spec §2 家長端）。所有 ``student_id`` 路徑都經
``get_owned_student`` / ``get_owned_student_for_write``（不屬於自己 / 不存在 / 封存皆同一 404；
寫入時退班小孩 409 ``student_not_active``）。寫入型 handler 呼叫 service 後自行 ``db.commit()``。

- BACKEND-441 ``GET /pickup/requests/today``：全部小孩今天的接送請求（BACKEND-416）。
- BACKEND-444 ``GET /children/{student_id}/pickup-persons``：常用接送人（BACKEND-417）。
- BACKEND-445 ``POST /children/{student_id}/pickup-persons``：multipart（name / relation / phone 為
  Form，組成 ``PickupPersonCreateIn`` 驗證；photo 為選填 File）→ BACKEND-418 → 201。
- BACKEND-446 ``DELETE /pickup-persons/{person_id}``：BACKEND-419 軟刪除（他人的與不存在同一
  404）→ 204。
- BACKEND-447 ``GET /children/{student_id}/pickup-authorizations``：BACKEND-421。
- BACKEND-448 ``POST /children/{student_id}/pickup-authorizations``：BACKEND-420 → 201，接送碼只在
  此回應出現一次，回應帶 ``Cache-Control: no-store``。
- BACKEND-440 ``POST /pickup/requests``（我要來接；``arrived: true`` 為「我已經到了」捷徑）：
  student_id 由 BACKEND-406 ``create_request`` 以 ``assert_parent_owns_student(for_write=True)``
  驗證 → 201 ``ParentPickupRequestOut``（含自動回覆）。
- BACKEND-442 ``POST /pickup/requests/{request_id}/arrived``：BACKEND-409（他人小孩的請求與不存在
  同一 404）→ 200。
- BACKEND-443 ``POST /pickup/requests/{request_id}/cancel``：body ``PickupCancelIn`` 可省略 →
  BACKEND-411 → 200。
- BACKEND-524 ``POST /pickup-authorizations/{auth_id}/regenerate-code``：BACKEND-523（舊碼立即失效、
  重設連錯與鎖定；他人的授權與不存在同一 404）→ 200，新接送碼只在此回應出現一次，回應帶
  ``Cache-Control: no-store``。
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile, status
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.api.deps import (
    CurrentParent,
    get_current_parent,
    get_owned_student,
    get_owned_student_for_write,
)
from app.core.clock import Clock, get_clock
from app.core.db import get_db
from app.core.request_meta import RequestMeta, get_request_meta
from app.core.storage import Storage, get_storage
from app.models.students import Student
from app.schemas.pickup import (
    ParentPickupRequestCreateIn,
    ParentPickupRequestOut,
    PickupAuthorizationCreatedOut,
    PickupAuthorizationCreateIn,
    PickupAuthorizationOut,
    PickupCancelIn,
    PickupPersonCreateIn,
    PickupPersonOut,
)
from app.services.audit_service import Actor
from app.services.pickup import authorizations, persons, requests
from app.services.pickup.views import build_parent_request_views

router = APIRouter(tags=["parent-pickup"])


@router.get("/pickup/requests/today", response_model=list[ParentPickupRequestOut])
def list_today_requests(
    parent: Annotated[CurrentParent, Depends(get_current_parent)],
    db: Annotated[Session, Depends(get_db)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> list[ParentPickupRequestOut]:
    return requests.list_today_requests_for_parent(db, parent.id, clock=clock)


@router.get("/children/{student_id}/pickup-persons", response_model=list[PickupPersonOut])
def list_pickup_persons(
    student: Annotated[Student, Depends(get_owned_student)],
    db: Annotated[Session, Depends(get_db)],
    storage: Annotated[Storage, Depends(get_storage)],
) -> list[PickupPersonOut]:
    return persons.list_pickup_persons(db, student.id, storage=storage)


def _person_form(
    name: Annotated[str, Form()],
    relation: Annotated[str, Form()],
    phone: Annotated[str, Form()],
) -> PickupPersonCreateIn:
    """multipart 欄位 → ``PickupPersonCreateIn``；驗證失敗轉成與 JSON body 相同的 422。"""
    try:
        return PickupPersonCreateIn(name=name, relation=relation, phone=phone)
    except ValidationError as exc:
        errors = [{**err, "loc": ("body", *err["loc"])} for err in exc.errors()]
        raise RequestValidationError(errors) from None


@router.post(
    "/children/{student_id}/pickup-persons",
    response_model=PickupPersonOut,
    status_code=status.HTTP_201_CREATED,
)
def create_pickup_person(
    student: Annotated[Student, Depends(get_owned_student_for_write)],
    parent: Annotated[CurrentParent, Depends(get_current_parent)],
    data: Annotated[PickupPersonCreateIn, Depends(_person_form)],
    db: Annotated[Session, Depends(get_db)],
    storage: Annotated[Storage, Depends(get_storage)],
    photo: Annotated[UploadFile | None, File()] = None,
) -> PickupPersonOut:
    out = persons.create_pickup_person(db, student.id, data, photo, parent=parent, storage=storage)
    db.commit()
    return out


@router.delete(
    "/pickup-persons/{person_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_pickup_person(
    person_id: UUID,
    parent: Annotated[CurrentParent, Depends(get_current_parent)],
    db: Annotated[Session, Depends(get_db)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> Response:
    persons.archive_pickup_person(db, person_id, parent=parent, clock=clock)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/children/{student_id}/pickup-authorizations", response_model=list[PickupAuthorizationOut]
)
def list_child_authorizations(
    student: Annotated[Student, Depends(get_owned_student)],
    db: Annotated[Session, Depends(get_db)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> list[PickupAuthorizationOut]:
    return authorizations.list_child_authorizations(db, student.id, clock=clock)


@router.post(
    "/children/{student_id}/pickup-authorizations",
    response_model=PickupAuthorizationCreatedOut,
    status_code=status.HTTP_201_CREATED,
)
def create_authorization(
    body: PickupAuthorizationCreateIn,
    response: Response,
    student: Annotated[Student, Depends(get_owned_student_for_write)],
    parent: Annotated[CurrentParent, Depends(get_current_parent)],
    db: Annotated[Session, Depends(get_db)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> PickupAuthorizationCreatedOut:
    out = authorizations.create_authorization(db, student.id, body, parent=parent, clock=clock)
    db.commit()
    # 接送碼明碼只回一次：禁止任何快取
    response.headers["Cache-Control"] = "no-store"
    return out


@router.post(
    "/pickup-authorizations/{auth_id}/regenerate-code", response_model=PickupAuthorizationCreatedOut
)
def regenerate_authorization_code(
    auth_id: UUID,
    response: Response,
    parent: Annotated[CurrentParent, Depends(get_current_parent)],
    meta: Annotated[RequestMeta, Depends(get_request_meta)],
    db: Annotated[Session, Depends(get_db)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> PickupAuthorizationCreatedOut:
    out = authorizations.regenerate_code(db, auth_id, parent=parent, meta=meta, clock=clock)
    db.commit()
    # 新接送碼明碼只回一次：禁止任何快取
    response.headers["Cache-Control"] = "no-store"
    return out


@router.post(
    "/pickup/requests", response_model=ParentPickupRequestOut, status_code=status.HTTP_201_CREATED
)
def create_pickup_request(
    body: ParentPickupRequestCreateIn,
    parent: Annotated[CurrentParent, Depends(get_current_parent)],
    db: Annotated[Session, Depends(get_db)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> ParentPickupRequestOut:
    request = requests.create_request(db, body, actor=Actor.parent(parent), clock=clock)
    out = build_parent_request_views(db, [request])[0]
    db.commit()
    return out


@router.post("/pickup/requests/{request_id}/arrived", response_model=ParentPickupRequestOut)
def mark_arrived(
    request_id: UUID,
    parent: Annotated[CurrentParent, Depends(get_current_parent)],
    db: Annotated[Session, Depends(get_db)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> ParentPickupRequestOut:
    request = requests.mark_arrived(db, request_id, parent=parent, clock=clock)
    out = build_parent_request_views(db, [request])[0]
    db.commit()
    return out


@router.post("/pickup/requests/{request_id}/cancel", response_model=ParentPickupRequestOut)
def cancel_pickup_request(
    request_id: UUID,
    parent: Annotated[CurrentParent, Depends(get_current_parent)],
    db: Annotated[Session, Depends(get_db)],
    clock: Annotated[Clock, Depends(get_clock)],
    body: PickupCancelIn | None = None,
) -> ParentPickupRequestOut:
    request = requests.cancel_request(db, request_id, body, actor=Actor.parent(parent), clock=clock)
    out = build_parent_request_views(db, [request])[0]
    db.commit()
    return out
