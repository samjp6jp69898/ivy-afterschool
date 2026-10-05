"""BACKEND-173：GET /api/admin/students/{student_id}/guardians。
BACKEND-177：POST /api/admin/guardians/{guardian_id}/binding-code。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.account import StaffUser
from app.models.audit import AuditLog
from app.models.parents import ParentBindingCode
from app.services.binding_code_service import CODE_ALPHABET, hash_code
from tests.support.factories import make_guardian, make_parent, make_student
from tests.support.fake_clock import FakeClock
from tests.support.route_audit import admin_routes_without_permission

StaffClientFactory = Callable[..., tuple[TestClient, StaffUser]]
AssertError = Callable[..., None]


def _url(student_id: object) -> str:
    return f"/api/admin/students/{student_id}/guardians"


def test_admin_guardians_list_success(
    staff_client: StaffClientFactory, db_session: Session, fake_clock: FakeClock
) -> None:
    student = make_student(db_session)
    parent = make_parent(db_session, display_name="王媽媽")
    primary = make_guardian(db_session, student, parent=parent, name="王媽媽", is_primary=True)
    pending = make_guardian(db_session, student, name="王爸爸", relation="father")
    make_guardian(db_session, student, name="已移除", relation="other", archived=True)
    client, staff = staff_client(permissions=["students:read"])
    expires = fake_clock.now() + timedelta(days=3)
    db_session.add(
        ParentBindingCode(
            guardian_id=pending.id,
            code_hash=uuid4().hex + uuid4().hex,
            expires_at=expires,
            created_by=staff.id,
            created_at=fake_clock.now() - timedelta(days=1),
        )
    )
    db_session.commit()

    resp = client.get(_url(student.id))

    assert resp.status_code == 200
    body = resp.json()
    assert [g["name"] for g in body] == ["王媽媽", "王爸爸"]
    assert body[0]["id"] == str(primary.id)
    assert body[0]["is_primary"] is True
    assert body[0]["binding"]["status"] == "bound"
    assert body[0]["binding"]["parent_display_name"] == "王媽媽"
    assert body[1]["binding"]["status"] == "code_issued"
    assert body[1]["binding"]["code_expires_at"] is not None
    assert set(body[0]) == {
        "id",
        "student_id",
        "name",
        "relation",
        "phone",
        "is_primary",
        "can_pickup",
        "receives_notifications",
        "binding",
    }
    # 綁定碼只存 hash，列表不得外洩
    assert "code" not in body[1]["binding"]


def test_admin_guardians_list_archived_student(
    staff_client: StaffClientFactory, db_session: Session
) -> None:
    student = make_student(db_session, archived=True)
    make_guardian(db_session, student)
    client, _ = staff_client(permissions=["students:read"])

    resp = client.get(_url(student.id))

    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_admin_guardians_list_422(
    staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    client, _ = staff_client(permissions=["students:read"])

    assert_error(client.get(_url("abc")), 422, "validation_error")


def test_admin_guardians_list_401(api_client: TestClient, assert_error: AssertError) -> None:
    assert_error(api_client.get(_url(uuid4())), 401, "unauthenticated")


def test_admin_guardians_list_403(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    student = make_student(db_session)
    client, _ = staff_client(permissions=["classes:read"])

    resp = client.get(_url(student.id))

    assert_error(resp, 403, "permission_denied")
    assert resp.json()["error"]["details"] == {"required": ["students:read"]}


def test_admin_guardians_list_404(
    staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    client, _ = staff_client(permissions=["students:read"])

    assert_error(client.get(_url(uuid4())), 404, "student_not_found")


def test_admin_guardians_list_guard_registered(app: FastAPI) -> None:
    assert admin_routes_without_permission(app) == []
    assert "/api/admin/students/{student_id}/guardians" in app.openapi()["paths"]


# --- BACKEND-177：POST /api/admin/guardians/{id}/binding-code -------------------------------------


def _binding_code_url(guardian_id: object) -> str:
    return f"/api/admin/guardians/{guardian_id}/binding-code"


def test_admin_binding_code_success(
    staff_client: StaffClientFactory, db_session: Session, fake_clock: FakeClock
) -> None:
    student = make_student(db_session)
    guardian = make_guardian(db_session, student, name="王爸爸", relation="father")
    client, staff = staff_client(permissions=["guardians:write"])

    resp = client.post(_binding_code_url(guardian.id))

    assert resp.status_code == 201
    body = resp.json()
    assert body["guardian_id"] == str(guardian.id)
    assert len(body["code"]) == 8
    assert set(body["code"]) <= set(CODE_ALPHABET)
    # pydantic 以 Z 結尾輸出 UTC，以解析後的 datetime 比較
    assert datetime.fromisoformat(body["expires_at"]) == fake_clock.now() + timedelta(days=7)
    assert set(body) == {"guardian_id", "code", "expires_at"}
    assert resp.headers["Cache-Control"] == "no-store"
    # 已 commit：DB 只存 hash、不存明碼；稽核已寫
    rows = list(
        db_session.execute(
            select(ParentBindingCode).where(ParentBindingCode.guardian_id == guardian.id)
        ).scalars()
    )
    assert len(rows) == 1
    assert rows[0].code_hash == hash_code(body["code"])
    assert rows[0].created_by == staff.id
    assert body["code"] not in rows[0].code_hash
    log = db_session.execute(
        select(AuditLog).where(
            AuditLog.action == "guardian.binding_code_issue",
            AuditLog.entity_id == str(guardian.id),
        )
    ).scalar_one()
    assert log.actor_id == staff.id
    assert body["code"] not in str(log.after)
    # 再產一次：舊碼作廢、只剩一筆有效
    second = client.post(_binding_code_url(guardian.id))
    assert second.status_code == 201
    assert second.json()["code"] != body["code"]
    db_session.expire_all()
    unused = db_session.execute(
        select(ParentBindingCode).where(
            ParentBindingCode.guardian_id == guardian.id, ParentBindingCode.used_at.is_(None)
        )
    ).scalars()
    assert len(list(unused)) == 1


def test_admin_binding_code_422(
    staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    client, _ = staff_client(permissions=["guardians:write"])

    assert_error(client.post(_binding_code_url("abc")), 422, "validation_error")


def test_admin_binding_code_401(api_client: TestClient, assert_error: AssertError) -> None:
    assert_error(api_client.post(_binding_code_url(uuid4())), 401, "unauthenticated")


def test_admin_binding_code_403(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    guardian = make_guardian(db_session, make_student(db_session))
    client, _ = staff_client(permissions=["students:write"])

    resp = client.post(_binding_code_url(guardian.id))

    assert_error(resp, 403, "permission_denied")
    assert resp.json()["error"]["details"] == {"required": ["guardians:write"]}
    assert (
        db_session.execute(
            select(ParentBindingCode).where(ParentBindingCode.guardian_id == guardian.id)
        ).first()
        is None
    )


def test_admin_binding_code_409(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    student = make_student(db_session)
    bound = make_guardian(db_session, student, parent=make_parent(db_session))
    archived_student_guardian = make_guardian(
        db_session, make_student(db_session, archived=True), name="封存學生的監護人"
    )
    client, _ = staff_client(permissions=["guardians:write"])

    assert_error(client.post(_binding_code_url(bound.id)), 409, "guardian_already_bound")
    assert_error(
        client.post(_binding_code_url(archived_student_guardian.id)), 409, "student_archived"
    )
    assert_error(client.post(_binding_code_url(uuid4())), 404, "guardian_not_found")


def test_admin_binding_code_guard_registered(app: FastAPI) -> None:
    assert admin_routes_without_permission(app) == []
    assert "post" in app.openapi()["paths"]["/api/admin/guardians/{guardian_id}/binding-code"]
