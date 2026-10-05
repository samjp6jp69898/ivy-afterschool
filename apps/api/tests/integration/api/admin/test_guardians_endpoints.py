"""BACKEND-173：GET /api/admin/students/{student_id}/guardians。
BACKEND-177：POST /api/admin/guardians/{guardian_id}/binding-code。
BACKEND-174：POST /api/admin/students/{student_id}/guardians。
BACKEND-175 / 176 / 178：PATCH / DELETE /api/admin/guardians/{id}、POST /{id}/unbind。"""

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
from app.models.parents import Guardian, ParentAccount, ParentBindingCode
from app.services.binding_code_service import CODE_ALPHABET, hash_code
from tests.support.factories import make_guardian, make_parent, make_student
from tests.support.fake_clock import FakeClock
from tests.support.route_audit import admin_routes_without_permission

StaffClientFactory = Callable[..., tuple[TestClient, StaffUser]]
ParentClientFactory = Callable[..., tuple[TestClient, ParentAccount]]
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


# --- BACKEND-174：POST /api/admin/students/{id}/guardians -----------------------------------------

_CREATE_GUARDIAN = {"name": "王爸爸", "relation": "father"}


def test_admin_guardians_create_success(
    staff_client: StaffClientFactory, db_session: Session
) -> None:
    student = make_student(db_session)
    make_guardian(db_session, student, name="王媽媽", is_primary=True)
    db_session.commit()
    client, _ = staff_client(permissions=["guardians:write"])

    resp = client.post(_url(student.id), json={**_CREATE_GUARDIAN, "phone": "0912-000-123"})

    assert resp.status_code == 201
    body = resp.json()
    assert (body["name"], body["relation"], body["phone"]) == ("王爸爸", "father", "0912-000-123")
    assert body["student_id"] == str(student.id)
    assert body["is_primary"] is False
    assert body["binding"]["status"] == "unbound"
    stored = db_session.execute(select(Guardian).where(Guardian.id == body["id"])).scalar_one()
    assert (stored.name, stored.relation, stored.student_id) == ("王爸爸", "father", student.id)


def test_admin_guardians_create_422(
    staff_client: StaffClientFactory, assert_error: AssertError, db_session: Session
) -> None:
    student = make_student(db_session)
    db_session.commit()
    client, _ = staff_client(permissions=["guardians:write"])

    assert_error(
        client.post(_url(student.id), json={**_CREATE_GUARDIAN, "relation": "aunt"}),
        422,
        "validation_error",
    )
    assert_error(client.post(_url(student.id), json={"name": "x"}), 422, "validation_error")
    assert_error(client.post(_url("abc"), json=_CREATE_GUARDIAN), 422, "validation_error")


def test_admin_guardians_create_401(api_client: TestClient, assert_error: AssertError) -> None:
    assert_error(api_client.post(_url(uuid4()), json=_CREATE_GUARDIAN), 401, "unauthenticated")


def test_admin_guardians_create_403(
    staff_client: StaffClientFactory, assert_error: AssertError, db_session: Session
) -> None:
    student = make_student(db_session)
    db_session.commit()
    client, _ = staff_client(permissions=["students:write"])

    resp = client.post(_url(student.id), json=_CREATE_GUARDIAN)

    assert_error(resp, 403, "permission_denied")
    assert resp.json()["error"]["details"] == {"required": ["guardians:write"]}


def test_admin_guardians_create_409(
    staff_client: StaffClientFactory, assert_error: AssertError, db_session: Session
) -> None:
    archived = make_student(db_session, archived=True)
    db_session.commit()
    client, _ = staff_client(permissions=["guardians:write"])

    assert_error(client.post(_url(archived.id), json=_CREATE_GUARDIAN), 409, "student_archived")
    assert_error(client.post(_url(uuid4()), json=_CREATE_GUARDIAN), 404, "student_not_found")


# --- BACKEND-175：PATCH /api/admin/guardians/{id} -------------------------------------------------


def _guardian_url(guardian_id: object) -> str:
    return f"/api/admin/guardians/{guardian_id}"


def test_admin_guardians_update_success(
    staff_client: StaffClientFactory, db_session: Session
) -> None:
    student = make_student(db_session)
    primary = make_guardian(db_session, student, name="王媽媽", is_primary=True)
    other = make_guardian(db_session, student, name="王爸爸", relation="father")
    db_session.commit()
    client, _ = staff_client(permissions=["guardians:write"])

    resp = client.patch(_guardian_url(other.id), json={"is_primary": True, "phone": "0912-000-002"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == str(other.id)
    assert body["is_primary"] is True
    assert body["phone"] == "0912-000-002"
    db_session.expire_all()
    assert (primary.is_primary, other.is_primary) == (False, True)
    assert other.phone == "0912-000-002"


def test_admin_guardians_update_422(
    staff_client: StaffClientFactory, assert_error: AssertError, db_session: Session
) -> None:
    guardian = make_guardian(db_session, make_student(db_session))
    db_session.commit()
    client, _ = staff_client(permissions=["guardians:write"])

    assert_error(client.patch(_guardian_url(guardian.id), json={}), 422, "validation_error")
    assert_error(
        client.patch(_guardian_url(guardian.id), json={"name": None}), 422, "validation_error"
    )
    assert_error(client.patch(_guardian_url("abc"), json={"name": "x"}), 422, "validation_error")


def test_admin_guardians_update_401(api_client: TestClient, assert_error: AssertError) -> None:
    assert_error(
        api_client.patch(_guardian_url(uuid4()), json={"name": "x"}), 401, "unauthenticated"
    )


def test_admin_guardians_update_403(
    staff_client: StaffClientFactory, assert_error: AssertError, db_session: Session
) -> None:
    guardian = make_guardian(db_session, make_student(db_session), name="王媽媽")
    db_session.commit()
    client, _ = staff_client(permissions=["students:write"])

    resp = client.patch(_guardian_url(guardian.id), json={"name": "改名"})

    assert_error(resp, 403, "permission_denied")
    db_session.expire_all()
    assert guardian.name == "王媽媽"


def test_admin_guardians_update_404(
    staff_client: StaffClientFactory, assert_error: AssertError, db_session: Session
) -> None:
    archived = make_guardian(db_session, make_student(db_session), archived=True)
    db_session.commit()
    client, _ = staff_client(permissions=["guardians:write"])

    assert_error(
        client.patch(_guardian_url(uuid4()), json={"name": "x"}), 404, "guardian_not_found"
    )
    assert_error(
        client.patch(_guardian_url(archived.id), json={"name": "x"}), 404, "guardian_not_found"
    )


# --- BACKEND-176：DELETE /api/admin/guardians/{id} ------------------------------------------------


def test_admin_guardians_delete_success(
    staff_client: StaffClientFactory, db_session: Session
) -> None:
    student = make_student(db_session)
    guardian = make_guardian(db_session, student, name="王媽媽", is_primary=True)
    keep = make_guardian(db_session, student, name="王爸爸", relation="father")
    db_session.commit()
    client, _ = staff_client(permissions=["guardians:write", "students:read"])

    resp = client.delete(_guardian_url(guardian.id))

    assert resp.status_code == 204
    assert resp.content == b""
    assert [g["id"] for g in client.get(_url(student.id)).json()] == [str(keep.id)]
    db_session.expire_all()
    assert guardian.archived_at is not None
    assert guardian.is_primary is False


def test_admin_guardians_delete_422(
    staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    client, _ = staff_client(permissions=["guardians:write"])

    assert_error(client.delete(_guardian_url("abc")), 422, "validation_error")


def test_admin_guardians_delete_401(api_client: TestClient, assert_error: AssertError) -> None:
    assert_error(api_client.delete(_guardian_url(uuid4())), 401, "unauthenticated")


def test_admin_guardians_delete_403(
    staff_client: StaffClientFactory, assert_error: AssertError, db_session: Session
) -> None:
    guardian = make_guardian(db_session, make_student(db_session))
    db_session.commit()
    client, _ = staff_client(permissions=["students:write"])

    assert_error(client.delete(_guardian_url(guardian.id)), 403, "permission_denied")
    db_session.expire_all()
    assert guardian.archived_at is None


def test_admin_guardians_delete_404(
    staff_client: StaffClientFactory, assert_error: AssertError, db_session: Session
) -> None:
    archived = make_guardian(db_session, make_student(db_session), archived=True)
    db_session.commit()
    client, _ = staff_client(permissions=["guardians:write"])

    assert_error(client.delete(_guardian_url(archived.id)), 404, "guardian_not_found")
    assert_error(client.delete(_guardian_url(uuid4())), 404, "guardian_not_found")


# --- BACKEND-178：POST /api/admin/guardians/{id}/unbind -------------------------------------------


def _unbind_url(guardian_id: object) -> str:
    return f"/api/admin/guardians/{guardian_id}/unbind"


def test_admin_unbind_success(
    staff_client: StaffClientFactory, parent_client: ParentClientFactory, db_session: Session
) -> None:
    parent, parent_account = parent_client()
    student = make_student(db_session, name="王小明")
    guardian = make_guardian(db_session, student, parent=parent_account, name="王媽媽")
    db_session.commit()
    assert [c["name"] for c in parent.get("/api/parent/children").json()] == ["王小明"]
    client, staff = staff_client(permissions=["guardians:write"])

    resp = client.post(_unbind_url(guardian.id))

    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == str(guardian.id)
    assert body["binding"]["status"] == "unbound"
    assert body["name"] == "王媽媽"
    db_session.expire_all()
    assert guardian.parent_account_id is None
    assert parent.get("/api/parent/children").json() == []
    audits = (
        db_session.execute(
            select(AuditLog).where(
                AuditLog.action == "guardian.unbind", AuditLog.entity_id == str(guardian.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(audits) == 1
    assert audits[0].actor_id == staff.id


def test_admin_unbind_422(staff_client: StaffClientFactory, assert_error: AssertError) -> None:
    client, _ = staff_client(permissions=["guardians:write"])

    assert_error(client.post(_unbind_url("abc")), 422, "validation_error")


def test_admin_unbind_401(api_client: TestClient, assert_error: AssertError) -> None:
    assert_error(api_client.post(_unbind_url(uuid4())), 401, "unauthenticated")


def test_admin_unbind_403(
    staff_client: StaffClientFactory, assert_error: AssertError, db_session: Session
) -> None:
    parent = make_parent(db_session)
    guardian = make_guardian(db_session, make_student(db_session), parent=parent)
    db_session.commit()
    client, _ = staff_client(permissions=["students:write"])

    assert_error(client.post(_unbind_url(guardian.id)), 403, "permission_denied")
    db_session.expire_all()
    assert guardian.parent_account_id == parent.id


def test_admin_unbind_409(
    staff_client: StaffClientFactory, assert_error: AssertError, db_session: Session
) -> None:
    unbound = make_guardian(db_session, make_student(db_session))
    db_session.commit()
    client, _ = staff_client(permissions=["guardians:write"])

    assert_error(client.post(_unbind_url(unbound.id)), 409, "guardian_not_bound")
    assert_error(client.post(_unbind_url(uuid4())), 404, "guardian_not_found")


def test_admin_guardians_write_guard_registered(app: FastAPI) -> None:
    assert admin_routes_without_permission(app) == []
    paths = app.openapi()["paths"]
    assert "post" in paths["/api/admin/students/{student_id}/guardians"]
    assert {"patch", "delete"} <= set(paths["/api/admin/guardians/{guardian_id}"])
    assert "post" in paths["/api/admin/guardians/{guardian_id}/unbind"]
