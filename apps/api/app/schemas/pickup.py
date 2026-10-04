"""BACKEND-402：接送模組 schemas。

時間點輸出 ISO8601 UTC；``HH:MM`` 欄位（TimeHM）為台北當地時間。家長端輸出不含員工姓名。
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, StringConstraints, model_validator

from app.core.settings_registry import TimeHM
from app.schemas.attendance import AttendanceStatus
from app.schemas.common import OutModel, RequestModel
from app.schemas.homework import OverallStatus

PHONE_PATTERN = r"^[0-9+\-() ]{8,20}$"
Phone = Annotated[str, StringConstraints(pattern=PHONE_PATTERN)]
PersonName = Annotated[str, Field(min_length=1, max_length=50)]
Note = Annotated[str, Field(min_length=1, max_length=200)]

RequestStatus = Literal["pending", "acknowledged", "arrived", "completed", "cancelled", "expired"]
RequestSource = Literal["parent", "staff", "proxy"]
RequesterType = Literal["parent", "staff"]
ReplySource = Literal["auto", "staff"]
CompletionMethod = Literal["guardian", "code", "visual_match", "override"]
AuthorizationStatus = Literal["active", "completed", "cancelled"]
EffectiveAuthorizationStatus = Literal["active", "completed", "cancelled", "expired"]
VerificationMethod = Literal["code", "visual_match", "override"]
LeaveType = Literal["sick", "personal", "other"]


# --- 請求（接送佇列）----------------------------------------------------------------------


class ParentPickupRequestCreateIn(RequestModel):
    student_id: UUID
    expected_arrival_at: TimeHM | None = None
    arrived: bool = False  # 「我已經到了」捷徑：建立後直接為 arrived

    @model_validator(mode="after")
    def _arrived_excludes_eta(self) -> Self:
        if self.arrived and self.expected_arrival_at is not None:
            raise ValueError("已到達時不可同時指定預計到達時間")
        return self


class StaffPickupRequestCreateIn(RequestModel):
    student_id: UUID
    expected_arrival_at: TimeHM | None = None


class PickupReplyIn(RequestModel):
    reply_ready_eta: TimeHM | None = None
    reply_message: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def _require_one(self) -> Self:
        if self.reply_ready_eta is None and self.reply_message is None:
            raise ValueError("reply_ready_eta 與 reply_message 至少要給一個")
        return self


class PickupCompleteIn(RequestModel):
    """代理接送的完成走 authorizations 端點，這裡只有監護人與強制完成。"""

    method: Literal["guardian", "override"]
    guardian_id: UUID | None = None
    note: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def _conditional_required(self) -> Self:
        if self.method == "guardian" and self.guardian_id is None:
            raise ValueError("method=guardian 時必須提供 guardian_id")
        if self.method == "override" and self.note is None:
            raise ValueError("method=override 時必須提供 note")
        return self


class PickupCancelIn(RequestModel):
    reason: str | None = Field(default=None, max_length=200)


class PickupQueueQuery(RequestModel):
    date: dt.date | None = None


class RosterQuery(RequestModel):
    date: dt.date | None = None
    class_id: UUID | None = None


# --- 回應（接送佇列）----------------------------------------------------------------------


class PickupStudentOut(OutModel):
    id: UUID
    student_no: str
    name: str
    grade_level: int
    class_id: UUID | None
    class_name: str | None


class PickupRequestOut(OutModel):
    id: UUID
    student: PickupStudentOut
    service_date: dt.date
    source: RequestSource
    requested_by_type: RequesterType
    requested_by_name: str | None
    expected_arrival_at: TimeHM | None
    status: RequestStatus
    homework_status_at_request: OverallStatus | None
    current_homework_status: OverallStatus | None
    current_ready_eta: TimeHM | None
    reply_ready_eta: TimeHM | None
    reply_message: str | None
    reply_source: ReplySource | None
    replied_at: dt.datetime | None
    replied_by_name: str | None
    # 非終態，且沒有回覆或只有「已通知老師」的自動回覆（沒有 ETA）
    needs_reply: bool
    arrived_at: dt.datetime | None
    completed_at: dt.datetime | None
    completed_by_name: str | None
    completion_method: CompletionMethod | None
    picked_up_by_name: str | None
    cancelled_at: dt.datetime | None
    cancel_reason: str | None
    created_at: dt.datetime


class ParentPickupRequestOut(OutModel):
    """家長端：不含員工姓名。"""

    id: UUID
    student_id: UUID
    student_name: str
    service_date: dt.date
    status: RequestStatus
    expected_arrival_at: TimeHM | None
    reply_ready_eta: TimeHM | None
    reply_message: str | None
    reply_source: ReplySource | None  # auto = 系統自動回覆，staff = 老師回覆
    replied_at: dt.datetime | None
    arrived_at: dt.datetime | None
    completed_at: dt.datetime | None
    picked_up_by_name: str | None
    cancelled_at: dt.datetime | None
    created_at: dt.datetime
    can_cancel: bool  # 非終態
    can_mark_arrived: bool  # pending / acknowledged


class PickupQueueCountsOut(OutModel):
    pending: int
    acknowledged: int
    arrived: int
    needs_reply: int


class PickupQueueOut(OutModel):
    date: dt.date
    open: list[PickupRequestOut]
    closed: list[PickupRequestOut]
    counts: PickupQueueCountsOut


class RosterOpenRequestOut(OutModel):
    id: UUID
    status: RequestStatus
    expected_arrival_at: TimeHM | None
    needs_reply: bool


class RosterStudentOut(OutModel):
    student_id: UUID
    student_no: str
    name: str
    grade_level: int
    attendance_status: AttendanceStatus | None
    check_in_at: dt.datetime | None
    check_out_at: dt.datetime | None
    leave_type: LeaveType | None
    homework_status: OverallStatus | None
    ready_eta: TimeHM | None
    open_request: RosterOpenRequestOut | None
    active_authorization_count: int


class RosterClassOut(OutModel):
    class_id: UUID | None  # None 為未分班
    class_name: str | None
    students: list[RosterStudentOut]


class RosterOut(OutModel):
    date: dt.date
    classes: list[RosterClassOut]


# --- 接送人 / 代理授權 --------------------------------------------------------------------


class PickupPersonCreateIn(RequestModel):
    """multipart form 欄位（照片檔另以 UploadFile 接收）。"""

    name: PersonName
    relation: str = Field(min_length=1, max_length=20)
    phone: Phone


class PickupPersonOut(OutModel):
    id: UUID
    student_id: UUID
    name: str
    relation: str
    phone: str
    photo_url: str | None
    created_at: dt.datetime


class PickupAuthorizationCreateIn(RequestModel):
    """恰好一種模式：指定常用接送人，或臨時代理（proxy_name 與 proxy_phone 皆必填）。"""

    service_date: dt.date
    pickup_person_id: UUID | None = None
    proxy_name: PersonName | None = None
    proxy_phone: Phone | None = None

    @model_validator(mode="after")
    def _exactly_one_mode(self) -> Self:
        if self.pickup_person_id is not None:
            if self.proxy_name is not None or self.proxy_phone is not None:
                raise ValueError("指定常用接送人時不可同時提供 proxy_name / proxy_phone")
        elif self.proxy_name is None or self.proxy_phone is None:
            raise ValueError("請指定常用接送人，或同時提供 proxy_name 與 proxy_phone")
        return self


class PickupAuthorizationOut(OutModel):
    id: UUID
    student_id: UUID
    service_date: dt.date
    pickup_person_id: UUID | None
    proxy_name: str
    proxy_phone: str
    code_last4: Annotated[str, StringConstraints(pattern=r"^[0-9]{4}$")]
    status: AuthorizationStatus
    effective_status: EffectiveAuthorizationStatus  # active 且 service_date < 今天 → expired
    verified_at: dt.datetime | None
    verification_method: VerificationMethod | None
    created_at: dt.datetime


class PickupAuthorizationCreatedOut(OutModel):
    authorization: PickupAuthorizationOut
    code: str  # 6 位明碼，只在建立時回傳一次


class StaffAuthorizationListQuery(RequestModel):
    date: dt.date | None = None
    status: AuthorizationStatus | None = None


class StaffAuthorizationOut(PickupAuthorizationOut):
    student: PickupStudentOut
    photo_url: str | None  # 常用接送人照片短效 URL
    code_attempts: int
    locked: bool
    verified_by_name: str | None


class VerifyCodeIn(RequestModel):
    # 允許空白與連字號，正規化在 service
    code: str = Field(min_length=4, max_length=20)


class OverrideCompleteIn(RequestModel):
    note: Note


class VisualMatchIn(RequestModel):
    note: str | None = Field(default=None, max_length=200)  # 例如「已核對身分證」


class AuthorizationCompleteOut(OutModel):
    authorization: StaffAuthorizationOut
    request: PickupRequestOut
