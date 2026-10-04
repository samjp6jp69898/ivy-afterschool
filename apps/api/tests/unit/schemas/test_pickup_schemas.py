"""BACKEND-402：接送模組 schemas。"""

from __future__ import annotations

import datetime as dt
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.pickup import (
    AuthorizationCompleteOut,
    OverrideCompleteIn,
    ParentPickupRequestCreateIn,
    ParentPickupRequestOut,
    PickupAuthorizationCreatedOut,
    PickupAuthorizationCreateIn,
    PickupAuthorizationOut,
    PickupCancelIn,
    PickupCompleteIn,
    PickupPersonCreateIn,
    PickupPersonOut,
    PickupQueueCountsOut,
    PickupQueueOut,
    PickupQueueQuery,
    PickupReplyIn,
    PickupRequestOut,
    PickupStudentOut,
    RosterClassOut,
    RosterOpenRequestOut,
    RosterOut,
    RosterQuery,
    RosterStudentOut,
    StaffAuthorizationListQuery,
    StaffAuthorizationOut,
    StaffPickupRequestCreateIn,
    VerifyCodeIn,
    VisualMatchIn,
)

_NOW = dt.datetime(2026, 9, 1, 9, 0, tzinfo=dt.UTC)
_DAY = dt.date(2026, 9, 1)


def _student_out() -> PickupStudentOut:
    return PickupStudentOut(
        id=uuid4(), student_no="S001", name="王小明", grade_level=3, class_id=None, class_name=None
    )


def _request_out(**overrides: Any) -> PickupRequestOut:
    data: dict[str, Any] = {
        "id": uuid4(),
        "student": _student_out(),
        "service_date": _DAY,
        "source": "parent",
        "requested_by_type": "parent",
        "requested_by_name": "王媽媽",
        "expected_arrival_at": "17:30",
        "status": "pending",
        "homework_status_at_request": "in_progress",
        "current_homework_status": "in_progress",
        "current_ready_eta": None,
        "reply_ready_eta": None,
        "reply_message": None,
        "reply_source": None,
        "replied_at": None,
        "replied_by_name": None,
        "needs_reply": True,
        "arrived_at": None,
        "completed_at": None,
        "completed_by_name": None,
        "completion_method": None,
        "picked_up_by_name": None,
        "cancelled_at": None,
        "cancel_reason": None,
        "created_at": _NOW,
    }
    data.update(overrides)
    return PickupRequestOut.model_validate(data)


def _auth_data(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": uuid4(),
        "student_id": uuid4(),
        "service_date": _DAY,
        "pickup_person_id": None,
        "proxy_name": "李阿姨",
        "proxy_phone": "0912-000-101",
        "code_last4": "3456",
        "status": "active",
        "effective_status": "active",
        "verified_at": None,
        "verification_method": None,
        "created_at": _NOW,
    }
    data.update(overrides)
    return data


def test_pickup_schemas_reply_requires_one() -> None:
    with pytest.raises(ValidationError):
        PickupReplyIn.model_validate({})
    with pytest.raises(ValidationError):
        PickupReplyIn.model_validate({"reply_ready_eta": None, "reply_message": None})
    assert PickupReplyIn.model_validate({"reply_ready_eta": "17:30"}).reply_ready_eta == "17:30"
    assert PickupReplyIn.model_validate({"reply_message": "請稍等"}).reply_message == "請稍等"
    with pytest.raises(ValidationError):
        PickupReplyIn(reply_message="x" * 201)
    with pytest.raises(ValidationError):
        PickupReplyIn(reply_message="")
    with pytest.raises(ValidationError):
        PickupReplyIn(reply_ready_eta="7:30")
    assert PickupReplyIn(reply_message="x" * 200).reply_message == "x" * 200


def test_pickup_schemas_complete_conditional() -> None:
    gid = uuid4()
    with pytest.raises(ValidationError):
        PickupCompleteIn(method="guardian")
    with pytest.raises(ValidationError):
        PickupCompleteIn(method="override")
    with pytest.raises(ValidationError):
        PickupCompleteIn.model_validate({"method": "code"})
    with pytest.raises(ValidationError):
        PickupCompleteIn(method="override", note="x" * 201)
    with pytest.raises(ValidationError):
        PickupCompleteIn(method="override", note="")
    assert PickupCompleteIn(method="guardian", guardian_id=gid).guardian_id == gid
    assert PickupCompleteIn(method="override", note="家長電話確認").note == "家長電話確認"


def test_pickup_schemas_authorization_modes() -> None:
    u = uuid4()
    base = {"service_date": "2026-09-01"}
    with pytest.raises(ValidationError):
        PickupAuthorizationCreateIn.model_validate(
            {**base, "pickup_person_id": u, "proxy_name": "李阿姨"}
        )
    with pytest.raises(ValidationError):
        PickupAuthorizationCreateIn.model_validate(
            {**base, "pickup_person_id": u, "proxy_phone": "0912-000-101"}
        )
    with pytest.raises(ValidationError):
        PickupAuthorizationCreateIn.model_validate({**base, "proxy_name": "李阿姨"})
    with pytest.raises(ValidationError):
        PickupAuthorizationCreateIn.model_validate({**base, "proxy_phone": "0912-000-101"})
    with pytest.raises(ValidationError):
        PickupAuthorizationCreateIn.model_validate({**base})
    with pytest.raises(ValidationError):
        PickupAuthorizationCreateIn.model_validate(
            {**base, "proxy_name": "李阿姨", "proxy_phone": "abc"}
        )
    with pytest.raises(ValidationError):
        PickupAuthorizationCreateIn.model_validate(
            {**base, "proxy_name": "", "proxy_phone": "0912-000-101"}
        )

    by_person = PickupAuthorizationCreateIn.model_validate({**base, "pickup_person_id": u})
    assert by_person.pickup_person_id == u
    assert by_person.proxy_name is None
    adhoc = PickupAuthorizationCreateIn.model_validate(
        {**base, "proxy_name": "李阿姨", "proxy_phone": "0912-000-101"}
    )
    assert adhoc.pickup_person_id is None
    assert adhoc.proxy_phone == "0912-000-101"


def test_pickup_schemas_phone_format() -> None:
    for bad in ("1234567", "x" * 8, "0912-000-101-000-0000", "０９１２０００１０１"):
        with pytest.raises(ValidationError):
            PickupPersonCreateIn(name="李阿姨", relation="阿姨", phone=bad)
    ok = PickupPersonCreateIn(name="李阿姨", relation="阿姨", phone="+886 (2) 1234-5678")
    assert ok.phone == "+886 (2) 1234-5678"
    for bad_fields in ({"name": ""}, {"name": "x" * 51}, {"relation": ""}, {"relation": "x" * 21}):
        with pytest.raises(ValidationError):
            PickupPersonCreateIn.model_validate(
                {"name": "李阿姨", "relation": "阿姨", "phone": "0912-000-101", **bad_fields}
            )


def test_pickup_schemas_extra() -> None:
    u = uuid4()
    with pytest.raises(ValidationError):
        ParentPickupRequestCreateIn.model_validate({"student_id": u, "note": "x"})
    with pytest.raises(ValidationError):
        StaffPickupRequestCreateIn.model_validate({"student_id": u, "note": "x"})
    with pytest.raises(ValidationError):
        PickupCancelIn.model_validate({"reason": "x", "extra": 1})
    with pytest.raises(ValidationError):
        VerifyCodeIn.model_validate({"code": "123456", "extra": 1})
    with pytest.raises(ValidationError):
        PickupQueueQuery.model_validate({"foo": 1})


def test_pickup_schemas_arrived_shortcut() -> None:
    u = uuid4()
    assert ParentPickupRequestCreateIn(student_id=u, arrived=True).arrived is True
    assert ParentPickupRequestCreateIn(student_id=u).arrived is False
    with pytest.raises(ValidationError):
        ParentPickupRequestCreateIn(student_id=u, arrived=True, expected_arrival_at="17:30")
    ok = ParentPickupRequestCreateIn(student_id=u, expected_arrival_at="17:30")
    assert ok.expected_arrival_at == "17:30"
    with pytest.raises(ValidationError):
        ParentPickupRequestCreateIn(student_id=u, expected_arrival_at="25:00")
    with pytest.raises(ValidationError):
        StaffPickupRequestCreateIn.model_validate({"student_id": u, "arrived": True})
    assert StaffPickupRequestCreateIn(student_id=u).expected_arrival_at is None


def test_pickup_schemas_cancel_body_optional() -> None:
    assert PickupCancelIn().reason is None
    assert PickupCancelIn(reason="改天").reason == "改天"
    with pytest.raises(ValidationError):
        PickupCancelIn(reason="x" * 201)


def test_pickup_schemas_code_and_notes() -> None:
    assert VerifyCodeIn(code="123 456").code == "123 456"
    assert VerifyCodeIn(code="123-456").code == "123-456"
    for bad in ("123", "1" * 21):
        with pytest.raises(ValidationError):
            VerifyCodeIn(code=bad)
    with pytest.raises(ValidationError):
        OverrideCompleteIn.model_validate({})
    with pytest.raises(ValidationError):
        OverrideCompleteIn(note="")
    assert OverrideCompleteIn(note="核對身分證").note == "核對身分證"
    assert VisualMatchIn().note is None
    assert VisualMatchIn(note="已核對身分證").note == "已核對身分證"
    with pytest.raises(ValidationError):
        VisualMatchIn(note="x" * 201)


def test_pickup_schemas_queries() -> None:
    assert PickupQueueQuery().date is None
    assert RosterQuery().class_id is None
    assert RosterQuery(date=_DAY, class_id=uuid4()).date == _DAY
    assert StaffAuthorizationListQuery().status is None
    assert StaffAuthorizationListQuery(status="active").status == "active"
    with pytest.raises(ValidationError):
        StaffAuthorizationListQuery(status="expired")


def test_pickup_schemas_request_out() -> None:
    out = _request_out(status="arrived", reply_source="auto", arrived_at=_NOW)

    body = out.model_dump(mode="json")
    assert body["student"]["name"] == "王小明"
    assert body["expected_arrival_at"] == "17:30"
    assert body["arrived_at"] == "2026-09-01T09:00:00Z"
    assert body["needs_reply"] is True
    for bad in ({"status": "unknown"}, {"reply_source": "robot"}, {"expected_arrival_at": "5pm"}):
        with pytest.raises(ValidationError):
            _request_out(**bad)

    queue = PickupQueueOut(
        date=_DAY,
        open=[out],
        closed=[],
        counts=PickupQueueCountsOut(pending=0, acknowledged=0, arrived=1, needs_reply=1),
    )
    assert queue.model_dump(mode="json")["counts"]["arrived"] == 1


def test_pickup_schemas_parent_out_has_no_staff_names() -> None:
    out = ParentPickupRequestOut(
        id=uuid4(),
        student_id=uuid4(),
        student_name="王小明",
        service_date=_DAY,
        status="pending",
        expected_arrival_at=None,
        reply_ready_eta="17:30",
        reply_message=None,
        reply_source="staff",
        replied_at=_NOW,
        arrived_at=None,
        completed_at=None,
        picked_up_by_name=None,
        cancelled_at=None,
        created_at=_NOW,
        can_cancel=True,
        can_mark_arrived=True,
    )

    fields = set(out.model_dump())
    assert not {f for f in fields if f.endswith("_by_name") and f != "picked_up_by_name"}
    assert "replied_by_name" not in fields
    assert "requested_by_name" not in fields
    assert out.reply_source == "staff"


def test_pickup_schemas_authorization_outputs() -> None:
    auth = PickupAuthorizationOut.model_validate(_auth_data(effective_status="expired"))
    assert auth.effective_status == "expired"
    with pytest.raises(ValidationError):
        PickupAuthorizationOut.model_validate(_auth_data(effective_status="gone"))
    with pytest.raises(ValidationError):
        PickupAuthorizationOut.model_validate(_auth_data(code_last4="12345"))

    created = PickupAuthorizationCreatedOut(authorization=auth, code="123456")
    assert created.code == "123456"
    assert "code" not in auth.model_dump()

    staff_auth = StaffAuthorizationOut.model_validate(
        {
            **_auth_data(),
            "student": _student_out(),
            "photo_url": None,
            "code_attempts": 5,
            "locked": True,
            "verified_by_name": None,
        }
    )
    assert staff_auth.locked is True
    assert (
        AuthorizationCompleteOut(authorization=staff_auth, request=_request_out()).request.status
        == "pending"
    )


def test_pickup_schemas_person_and_roster_outputs() -> None:
    person = PickupPersonOut(
        id=uuid4(),
        student_id=uuid4(),
        name="李阿姨",
        relation="阿姨",
        phone="0912-000-101",
        photo_url=None,
        created_at=_NOW,
    )
    assert person.photo_url is None

    roster = RosterOut(
        date=_DAY,
        classes=[
            RosterClassOut(
                class_id=None,
                class_name=None,
                students=[
                    RosterStudentOut(
                        student_id=uuid4(),
                        student_no="S001",
                        name="王小明",
                        grade_level=3,
                        attendance_status="present",
                        check_in_at=_NOW,
                        check_out_at=None,
                        leave_type=None,
                        homework_status="done",
                        ready_eta=None,
                        open_request=RosterOpenRequestOut(
                            id=uuid4(),
                            status="pending",
                            expected_arrival_at=None,
                            needs_reply=True,
                        ),
                        active_authorization_count=2,
                    )
                ],
            )
        ],
    )
    body = roster.model_dump(mode="json")
    assert body["classes"][0]["class_id"] is None
    assert body["classes"][0]["students"][0]["active_authorization_count"] == 2
