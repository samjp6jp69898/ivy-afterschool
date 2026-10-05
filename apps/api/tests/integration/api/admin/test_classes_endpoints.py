"""BACKEND-141：GET /api/admin/classes。
BACKEND-142：POST /api/admin/classes（classes:write）。
BACKEND-143：GET /api/admin/classes/{class_id}（classes:read）。
BACKEND-144 / 145 / 146：PATCH /{class_id}、POST /{class_id}/archive、PUT /{class_id}/staff
（classes:write）。"""

from __future__ import annotations

from collections.abc import Callable
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.account import StaffUser
from app.models.classes import SchoolClass
from tests.support.factories import make_class, make_class_staff, make_staff, make_student
from tests.support.route_audit import admin_routes_without_permission

_URL = "/api/admin/classes"
StaffClientFactory = Callable[..., tuple[TestClient, StaffUser]]
AssertError = Callable[..., None]


def test_admin_classes_list_success(staff_client: StaffClientFactory, db_session: Session) -> None:
    klass = make_class(db_session, name="A班", grade_levels=(1, 2), academic_year=115)
    make_student(db_session, class_=klass)
    make_student(db_session, class_=klass)
    client, staff = staff_client(permissions=["classes:read"])
    make_class_staff(db_session, klass, staff, role="lead")
    db_session.commit()

    resp = client.get(_URL, params={"academic_year": 115})

    assert resp.status_code == 200
    item = next(c for c in resp.json() if c["id"] == str(klass.id))
    assert item["name"] == "A班"
    assert item["student_count"] == 2
    assert item["grade_levels"] == [1, 2]
    assert [s["display_name"] for s in item["staff"]] == [staff.display_name]
    assert set(item) == {
        "id",
        "name",
        "grade_levels",
        "academic_year",
        "sort_order",
        "archived_at",
        "student_count",
        "staff",
    }


def test_admin_classes_list_mine(staff_client: StaffClientFactory, db_session: Session) -> None:
    mine = make_class(db_session, name="我的班")
    make_class(db_session, name="別人的班")
    client, staff = staff_client(permissions=["classes:read"])
    make_class_staff(db_session, mine, staff, role="assistant")
    db_session.commit()

    resp = client.get(_URL, params={"mine": "true"})

    assert [c["id"] for c in resp.json()] == [str(mine.id)]
    assert len(client.get(_URL).json()) >= 2


def test_admin_classes_list_archived(staff_client: StaffClientFactory, db_session: Session) -> None:
    archived = make_class(db_session, name="舊班", archived=True)
    client, _ = staff_client(permissions=["classes:read"])

    default = {c["id"] for c in client.get(_URL).json()}
    with_archived = {c["id"] for c in client.get(_URL, params={"include_archived": "true"}).json()}

    assert str(archived.id) not in default
    assert str(archived.id) in with_archived


def test_admin_classes_list_422(
    staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    client, _ = staff_client(permissions=["classes:read"])

    for params in ({"academic_year": "abc"}, {"mine": "maybe"}, {"foo": "bar"}):
        assert_error(client.get(_URL, params=params), 422, "validation_error")


def test_admin_classes_list_401(api_client: TestClient, assert_error: AssertError) -> None:
    assert_error(api_client.get(_URL), 401, "unauthenticated")


def test_admin_classes_list_403(
    staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    client, _ = staff_client(permissions=["students:read"])

    resp = client.get(_URL)

    assert_error(resp, 403, "permission_denied")
    assert resp.json()["error"]["details"] == {"required": ["classes:read"]}


def test_admin_classes_list_guard_registered(app: FastAPI) -> None:
    assert admin_routes_without_permission(app) == []
    assert "/api/admin/classes" in app.openapi()["paths"]


# --- BACKEND-142：POST /api/admin/classes ---------------------------------------------------------

_CREATE_PAYLOAD = {"name": "低年級 A 班", "grade_levels": [1, 2], "academic_year": 115}


def test_admin_classes_create_success(
    staff_client: StaffClientFactory, db_session: Session
) -> None:
    client, _ = staff_client(permissions=["classes:write"])

    resp = client.post(_URL, json=_CREATE_PAYLOAD)

    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "低年級 A 班"
    assert body["grade_levels"] == [1, 2]
    assert body["academic_year"] == 115
    assert body["sort_order"] == 0
    assert body["archived_at"] is None
    assert body["student_count"] == 0
    assert body["staff"] == []
    stored = db_session.execute(
        select(SchoolClass).where(SchoolClass.id == body["id"])
    ).scalar_one()
    assert stored.name == "低年級 A 班"


def test_admin_classes_create_422(
    staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    client, _ = staff_client(permissions=["classes:write"])

    for payload in (
        {**_CREATE_PAYLOAD, "grade_levels": [7]},
        {**_CREATE_PAYLOAD, "grade_levels": []},
        {**_CREATE_PAYLOAD, "foo": 1},
        {**_CREATE_PAYLOAD, "name": ""},
        {**_CREATE_PAYLOAD, "academic_year": 99},
        {},
    ):
        assert_error(client.post(_URL, json=payload), 422, "validation_error")


def test_admin_classes_create_401(api_client: TestClient, assert_error: AssertError) -> None:
    assert_error(api_client.post(_URL, json=_CREATE_PAYLOAD), 401, "unauthenticated")


def test_admin_classes_create_403(
    staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    client, _ = staff_client(permissions=["classes:read"])

    resp = client.post(_URL, json=_CREATE_PAYLOAD)

    assert_error(resp, 403, "permission_denied")
    assert resp.json()["error"]["details"] == {"required": ["classes:write"]}


def test_admin_classes_create_409(
    staff_client: StaffClientFactory, assert_error: AssertError, db_session: Session
) -> None:
    make_class(db_session, name="低年級 A 班", academic_year=115)
    db_session.commit()
    client, _ = staff_client(permissions=["classes:write"])

    dup = client.post(_URL, json={**_CREATE_PAYLOAD, "name": " 低年級 a 班 "})
    other_year = client.post(_URL, json={**_CREATE_PAYLOAD, "academic_year": 116})

    assert_error(dup, 409, "class_name_taken")
    assert other_year.status_code == 201


def test_admin_classes_create_guard_registered(app: FastAPI) -> None:
    assert admin_routes_without_permission(app) == []
    assert "post" in app.openapi()["paths"]["/api/admin/classes"]


# --- BACKEND-143：GET /api/admin/classes/{class_id} -----------------------------------------------


def test_admin_classes_get_success(staff_client: StaffClientFactory, db_session: Session) -> None:
    klass = make_class(db_session, name="低年級A班", grade_levels=(1, 2), academic_year=115)
    archived = make_class(db_session, name="舊班", archived=True)
    client, _ = staff_client(permissions=["classes:read"])

    resp = client.get(f"{_URL}/{klass.id}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == str(klass.id)
    assert body["name"] == "低年級A班"
    assert body["grade_levels"] == [1, 2]
    assert body["academic_year"] == 115
    assert body["student_count"] == 0
    assert body["staff"] == []
    assert body["archived_at"] is None
    # 封存班也可查
    assert client.get(f"{_URL}/{archived.id}").json()["archived_at"] is not None


def test_admin_classes_get_422(staff_client: StaffClientFactory, assert_error: AssertError) -> None:
    client, _ = staff_client(permissions=["classes:read"])

    assert_error(client.get(f"{_URL}/abc"), 422, "validation_error")


def test_admin_classes_get_401(api_client: TestClient, assert_error: AssertError) -> None:
    assert_error(api_client.get(f"{_URL}/{uuid4()}"), 401, "unauthenticated")


def test_admin_classes_get_403(
    staff_client: StaffClientFactory, assert_error: AssertError, db_session: Session
) -> None:
    klass = make_class(db_session)
    client, _ = staff_client(permissions=["classes:write"])

    resp = client.get(f"{_URL}/{klass.id}")

    assert_error(resp, 403, "permission_denied")
    assert resp.json()["error"]["details"] == {"required": ["classes:read"]}


def test_admin_classes_get_404(staff_client: StaffClientFactory, assert_error: AssertError) -> None:
    client, _ = staff_client(permissions=["classes:read"])

    assert_error(client.get(f"{_URL}/{uuid4()}"), 404, "class_not_found")


def test_admin_classes_get_guard_registered(app: FastAPI) -> None:
    assert admin_routes_without_permission(app) == []
    assert "get" in app.openapi()["paths"]["/api/admin/classes/{class_id}"]


# --- BACKEND-144 / 145 / 146：PATCH /{id}、POST /{id}/archive、PUT /{id}/staff ------------------


def _class_url(class_id: object, suffix: str = "") -> str:
    return f"{_URL}/{class_id}{suffix}"


def test_admin_classes_update_success(
    staff_client: StaffClientFactory, db_session: Session
) -> None:
    klass = make_class(db_session, name="A班")
    client, _ = staff_client(permissions=["classes:write"])

    resp = client.patch(_class_url(klass.id), json={"sort_order": 2})

    assert resp.status_code == 200
    body = resp.json()
    assert body["sort_order"] == 2
    assert body["name"] == "A班"
    db_session.expire_all()
    assert klass.sort_order == 2
    renamed = client.patch(_class_url(klass.id), json={"name": "A+班", "grade_levels": [3, 4]})
    assert (renamed.json()["name"], renamed.json()["grade_levels"]) == ("A+班", [3, 4])


def test_admin_classes_update_422(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    klass = make_class(db_session)
    client, _ = staff_client(permissions=["classes:write"])

    assert_error(client.patch(_class_url(klass.id), json={}), 422, "validation_error")
    assert_error(
        client.patch(_class_url(klass.id), json={"archived_at": None}), 422, "validation_error"
    )
    assert_error(client.patch(_class_url("abc"), json={"sort_order": 1}), 422, "validation_error")


def test_admin_classes_update_401(api_client: TestClient, assert_error: AssertError) -> None:
    assert_error(
        api_client.patch(_class_url(uuid4()), json={"sort_order": 1}), 401, "unauthenticated"
    )


def test_admin_classes_update_403(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    klass = make_class(db_session)
    client, _ = staff_client(permissions=["classes:read"])

    resp = client.patch(_class_url(klass.id), json={"sort_order": 1})

    assert_error(resp, 403, "permission_denied")
    assert resp.json()["error"]["details"] == {"required": ["classes:write"]}


def test_admin_classes_update_409(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    archived = make_class(db_session, name="舊班", archived=True)
    client, _ = staff_client(permissions=["classes:write"])

    assert_error(
        client.patch(_class_url(archived.id), json={"sort_order": 1}), 409, "class_archived"
    )
    assert_error(client.patch(_class_url(uuid4()), json={"sort_order": 1}), 404, "class_not_found")


def test_admin_classes_archive_success(
    staff_client: StaffClientFactory, db_session: Session
) -> None:
    klass = make_class(db_session, name="畢業班")
    client, _ = staff_client(permissions=["classes:write"])

    resp = client.post(_class_url(klass.id, "/archive"))

    assert resp.status_code == 200
    assert resp.json()["archived_at"] is not None
    db_session.expire_all()
    assert klass.archived_at is not None
    # 冪等
    again = client.post(_class_url(klass.id, "/archive"))
    assert again.status_code == 200
    assert again.json()["archived_at"] == resp.json()["archived_at"]


def test_admin_classes_archive_422(
    staff_client: StaffClientFactory, assert_error: AssertError
) -> None:
    client, _ = staff_client(permissions=["classes:write"])

    assert_error(client.post(_class_url("abc", "/archive")), 422, "validation_error")


def test_admin_classes_archive_401(api_client: TestClient, assert_error: AssertError) -> None:
    assert_error(api_client.post(_class_url(uuid4(), "/archive")), 401, "unauthenticated")


def test_admin_classes_archive_403(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    klass = make_class(db_session)
    client, _ = staff_client(permissions=["classes:read"])

    assert_error(client.post(_class_url(klass.id, "/archive")), 403, "permission_denied")
    db_session.expire_all()
    assert klass.archived_at is None


def test_admin_classes_archive_409(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    klass = make_class(db_session)
    make_student(db_session, class_=klass)
    client, _ = staff_client(permissions=["classes:write"])

    resp = client.post(_class_url(klass.id, "/archive"))

    assert_error(resp, 409, "class_has_students")
    assert_error(client.post(_class_url(uuid4(), "/archive")), 404, "class_not_found")
    db_session.expire_all()
    assert klass.archived_at is None


def test_admin_classes_staff_success(staff_client: StaffClientFactory, db_session: Session) -> None:
    klass = make_class(db_session)
    s1 = make_staff(db_session, role_code="tutor", display_name="林老師")
    s2 = make_staff(db_session, role_code="tutor", display_name="陳老師")
    client, _ = staff_client(permissions=["classes:write"])

    resp = client.put(
        _class_url(klass.id, "/staff"),
        json={
            "items": [
                {"staff_user_id": str(s1.id), "role": "lead"},
                {"staff_user_id": str(s2.id), "role": "assistant"},
            ]
        },
    )

    assert resp.status_code == 200
    staff = resp.json()["staff"]
    assert staff[0] == {"staff_user_id": str(s1.id), "display_name": "林老師", "role": "lead"}
    assert [s["role"] for s in staff] == ["lead", "assistant"]
    # 整批取代：只剩 s2
    replaced = client.put(
        _class_url(klass.id, "/staff"),
        json={"items": [{"staff_user_id": str(s2.id), "role": "lead"}]},
    )
    assert [s["staff_user_id"] for s in replaced.json()["staff"]] == [str(s2.id)]
    assert client.put(_class_url(klass.id, "/staff"), json={"items": []}).json()["staff"] == []


def test_admin_classes_staff_422(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    klass = make_class(db_session)
    s1 = make_staff(db_session, role_code="tutor")
    inactive = make_staff(db_session, role_code="tutor", is_active=False)
    client, _ = staff_client(permissions=["classes:write"])

    bad_role = client.put(
        _class_url(klass.id, "/staff"),
        json={"items": [{"staff_user_id": str(s1.id), "role": "owner"}]},
    )
    bad_staff = client.put(
        _class_url(klass.id, "/staff"),
        json={"items": [{"staff_user_id": str(inactive.id), "role": "lead"}]},
    )
    missing = client.put(_class_url(klass.id, "/staff"), json={})

    assert_error(bad_role, 422, "validation_error")
    assert_error(bad_staff, 422, "invalid_staff")
    assert bad_staff.json()["error"]["details"] == {"invalid": [str(inactive.id)]}
    assert_error(missing, 422, "validation_error")


def test_admin_classes_staff_401(api_client: TestClient, assert_error: AssertError) -> None:
    assert_error(
        api_client.put(_class_url(uuid4(), "/staff"), json={"items": []}), 401, "unauthenticated"
    )


def test_admin_classes_staff_403(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    klass = make_class(db_session)
    client, _ = staff_client(permissions=["classes:read"])

    assert_error(
        client.put(_class_url(klass.id, "/staff"), json={"items": []}), 403, "permission_denied"
    )


def test_admin_classes_staff_409(
    staff_client: StaffClientFactory, db_session: Session, assert_error: AssertError
) -> None:
    archived = make_class(db_session, archived=True)
    s1 = make_staff(db_session, role_code="tutor")
    client, _ = staff_client(permissions=["classes:write"])

    resp = client.put(
        _class_url(archived.id, "/staff"),
        json={"items": [{"staff_user_id": str(s1.id), "role": "lead"}]},
    )

    assert_error(resp, 409, "class_archived")
    assert_error(
        client.put(_class_url(uuid4(), "/staff"), json={"items": []}), 404, "class_not_found"
    )


def test_admin_classes_write_guard_registered(app: FastAPI) -> None:
    assert admin_routes_without_permission(app) == []
    paths = app.openapi()["paths"]
    assert "patch" in paths["/api/admin/classes/{class_id}"]
    assert "post" in paths["/api/admin/classes/{class_id}/archive"]
    assert "put" in paths["/api/admin/classes/{class_id}/staff"]
