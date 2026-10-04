"""BACKEND-179：app/services/parent_scope.py（get_parent_student_ids，家長可見學生範圍）。
BACKEND-180：``assert_parent_owns_student`` 與 ``get_owned_student`` / ``get_owned_student_for_write``
dependency（IDOR 一律 404，不洩漏存在與否；for_write 對 withdrawn 409）。

domain_spec M3：``guardians.parent_account_id = 自己`` 且 guardian / student 皆未封存；withdrawn
仍可見；每次呼叫都查 DB。這是家長端 IDOR 防護的根基。
"""

from collections.abc import Iterator
from datetime import date
from typing import Annotated
from uuid import uuid4

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.deps import get_owned_student, get_owned_student_for_write
from app.core.clock import get_clock
from app.core.config import get_settings
from app.core.crypto import derive_key
from app.core.db import get_db
from app.core.errors import AppError, register_exception_handlers
from app.models.students import Student
from app.services.parent_scope import assert_parent_owns_student, get_parent_student_ids
from tests.support.auth_cookies import login_parent
from tests.support.db_override import override_get_db
from tests.support.factories import make_guardian, make_parent, make_student
from tests.support.fake_clock import FakeClock


def test_parent_student_ids_basic(db_session: Session) -> None:
    p = make_parent(db_session)
    ming = make_student(db_session, name="王小明")
    hua = make_student(db_session, name="陳小華")
    an = make_student(db_session, name="林小安", archived=True)
    make_guardian(db_session, ming, parent=p)
    make_guardian(db_session, hua, parent=p, archived=True)
    make_guardian(db_session, an, parent=p)
    # 未綁定家長帳號的監護人不算
    make_guardian(db_session, make_student(db_session, name="張小芳"), parent=None)

    assert get_parent_student_ids(db_session, p.id) == [ming.id]


def test_parent_student_ids_withdrawn_visible(db_session: Session) -> None:
    p = make_parent(db_session)
    ming = make_student(db_session, name="王小明")
    make_guardian(db_session, ming, parent=p)
    # DB CHECK：withdrawn 必須有 withdrawn_on
    ming.status = "withdrawn"
    ming.withdrawn_on = date(2026, 7, 31)
    db_session.flush()

    assert get_parent_student_ids(db_session, p.id) == [ming.id]


def test_parent_student_ids_isolation(db_session: Session) -> None:
    p = make_parent(db_session)
    q = make_parent(db_session)
    ming = make_student(db_session, name="王小明")
    hua = make_student(db_session, name="陳小華")
    make_guardian(db_session, ming, parent=p)
    make_guardian(db_session, hua, parent=q)

    assert get_parent_student_ids(db_session, p.id) == [ming.id]
    assert get_parent_student_ids(db_session, q.id) == [hua.id]
    assert get_parent_student_ids(db_session, uuid4()) == []


def test_parent_student_ids_ordered_and_distinct(db_session: Session) -> None:
    """同一學生有舊的（已封存）與現行的綁定時只回一次；依姓名、student_no 排序。

    同一家長對同一學生的未封存綁定由 DB partial unique index 擋，重複只可能來自封存列。
    """
    p = make_parent(db_session)
    b1 = make_student(db_session, name="李小兵", student_no="S-ORD-002")
    b0 = make_student(db_session, name="李小兵", student_no="S-ORD-001")
    a = make_student(db_session, name="丁小安", student_no="S-ORD-009")
    make_guardian(db_session, b1, parent=p, name="李爸爸", relation="father", archived=True)
    make_guardian(db_session, b1, parent=p, name="李媽媽", relation="mother")
    make_guardian(db_session, b0, parent=p)
    make_guardian(db_session, a, parent=p)

    assert get_parent_student_ids(db_session, p.id) == [a.id, b0.id, b1.id]


def test_parent_student_ids_unbind_immediately(db_session: Session) -> None:
    """解除綁定（guardian.parent_account_id 清空或封存）立即生效，不快取。"""
    p = make_parent(db_session)
    ming = make_student(db_session, name="王小明")
    g = make_guardian(db_session, ming, parent=p)
    assert get_parent_student_ids(db_session, p.id) == [ming.id]

    g.parent_account_id = None
    db_session.flush()
    assert get_parent_student_ids(db_session, p.id) == []


# --- BACKEND-180：assert_parent_owns_student 與 get_owned_student dependency（IDOR → 404） ----


def _assert_student_not_found(exc: AppError) -> None:
    assert exc.status == 404
    assert exc.code == "student_not_found"
    assert exc.message == "找不到學生"
    assert exc.details is None


def test_assert_owns_student_ok(db_session: Session) -> None:
    p = make_parent(db_session)
    ming = make_student(db_session, name="王小明")
    make_guardian(db_session, ming, parent=p)

    student = assert_parent_owns_student(db_session, p.id, ming.id)

    assert student.id == ming.id
    assert student.name == "王小明"
    assert assert_parent_owns_student(db_session, p.id, ming.id, for_write=True).id == ming.id


def test_assert_owns_student_idor_same_as_missing(db_session: Session) -> None:
    """他人小孩與不存在的 uuid 回完全相同的 404（status、code、message），不洩漏存在與否。"""
    p = make_parent(db_session)
    q = make_parent(db_session)
    hua = make_student(db_session, name="陳小華")
    make_guardian(db_session, hua, parent=q)

    with pytest.raises(AppError) as other:
        assert_parent_owns_student(db_session, p.id, hua.id)
    with pytest.raises(AppError) as missing:
        assert_parent_owns_student(db_session, p.id, uuid4())

    _assert_student_not_found(other.value)
    _assert_student_not_found(missing.value)
    assert (other.value.status, other.value.code, other.value.message, other.value.details) == (
        missing.value.status,
        missing.value.code,
        missing.value.message,
        missing.value.details,
    )
    # for_write 也不洩漏
    with pytest.raises(AppError) as other_write:
        assert_parent_owns_student(db_session, p.id, hua.id, for_write=True)
    _assert_student_not_found(other_write.value)


def test_assert_owns_student_archived(db_session: Session) -> None:
    p = make_parent(db_session)
    ming = make_student(db_session, name="王小明")
    make_guardian(db_session, ming, parent=p, archived=True)
    an = make_student(db_session, name="林小安", archived=True)
    make_guardian(db_session, an, parent=p)

    with pytest.raises(AppError) as guardian_archived:
        assert_parent_owns_student(db_session, p.id, ming.id)
    _assert_student_not_found(guardian_archived.value)
    with pytest.raises(AppError) as student_archived:
        assert_parent_owns_student(db_session, p.id, an.id)
    _assert_student_not_found(student_archived.value)


def test_assert_owns_student_for_write_withdrawn(db_session: Session) -> None:
    p = make_parent(db_session)
    ming = make_student(db_session, name="王小明")
    make_guardian(db_session, ming, parent=p)
    ming.status = "withdrawn"
    ming.withdrawn_on = date(2026, 7, 31)
    db_session.flush()

    assert assert_parent_owns_student(db_session, p.id, ming.id, for_write=False).id == ming.id

    with pytest.raises(AppError) as exc:
        assert_parent_owns_student(db_session, p.id, ming.id, for_write=True)
    assert exc.value.status == 409
    assert exc.value.code == "student_not_active"
    assert exc.value.message == "此學生已退班，無法進行此操作"

    # suspended 不算退班，寫入仍允許
    ming.status = "suspended"
    ming.withdrawn_on = None
    db_session.flush()
    assert assert_parent_owns_student(db_session, p.id, ming.id, for_write=True).id == ming.id


@pytest.fixture
def auth_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@127.0.0.1:54342/postgres")
    monkeypatch.setenv("APP_SECRET_KEY", "s" * 48)
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://127.0.0.1:5341")
    monkeypatch.setenv("R2_ENDPOINT_URL", "http://127.0.0.1:54344")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "afterschool")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "afterschool-local-secret")
    monkeypatch.setenv("R2_BUCKET", "afterschool-local")
    get_settings.cache_clear()
    derive_key.cache_clear()
    yield
    get_settings.cache_clear()
    derive_key.cache_clear()


@pytest.mark.usefixtures("auth_env")
def test_get_owned_student_dependency(db_session: Session, fake_clock: FakeClock) -> None:
    app = FastAPI()
    register_exception_handlers(app)
    app.dependency_overrides[get_db] = override_get_db(db_session)
    app.dependency_overrides[get_clock] = lambda: fake_clock

    @app.get("/api/parent/x/{student_id}")
    def read_probe(student: Annotated[Student, Depends(get_owned_student)]) -> dict[str, str]:
        return {"id": str(student.id), "name": student.name}

    @app.post("/api/parent/w/{student_id}")
    def write_probe(
        student: Annotated[Student, Depends(get_owned_student_for_write)],
    ) -> dict[str, str]:
        return {"id": str(student.id)}

    p = make_parent(db_session)
    q = make_parent(db_session)
    ming = make_student(db_session, name="王小明")
    hua = make_student(db_session, name="陳小華")
    make_guardian(db_session, ming, parent=p)
    make_guardian(db_session, hua, parent=q)
    hua.status = "withdrawn"
    hua.withdrawn_on = date(2026, 7, 31)
    db_session.commit()

    with TestClient(app) as client:
        login_parent(client, p, clock=fake_clock)

        ok = client.get(f"/api/parent/x/{ming.id}")
        assert ok.status_code == 200
        assert ok.json() == {"id": str(ming.id), "name": "王小明"}

        other = client.get(f"/api/parent/x/{hua.id}")
        missing = client.get(f"/api/parent/x/{uuid4()}")
        assert other.status_code == 404
        assert missing.status_code == 404
        assert other.json() == missing.json()
        assert other.json()["error"]["code"] == "student_not_found"

        assert client.post(f"/api/parent/w/{ming.id}").status_code == 200
        # 他人的退班學生：仍是 404（不洩漏），不是 409
        assert client.post(f"/api/parent/w/{hua.id}").json() == missing.json()

        # 未登入 → 401（不走到 student 查詢）
        anonymous = TestClient(app)
        assert anonymous.get(f"/api/parent/x/{ming.id}").status_code == 401
        assert anonymous.get(f"/api/parent/x/{ming.id}").json()["error"]["code"] == (
            "unauthenticated"
        )

        # 自己的退班學生：讀 200、寫 409
        ming.status = "withdrawn"
        ming.withdrawn_on = date(2026, 7, 31)
        db_session.commit()
        assert client.get(f"/api/parent/x/{ming.id}").status_code == 200
        conflict = client.post(f"/api/parent/w/{ming.id}")
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "student_not_active"
