"""BACKEND-054：app/services/binding_code_service.py（generate、normalize_code、hash_code）。
BACKEND-055：claim（原子兌換、診斷 invalid / expired / used、條件式綁定 guardian、重複綁同學生
409）。

8 碼綁定碼：明碼只回傳一次、DB 只存 HMAC、產新碼作廢舊的未使用碼、7 天效期、寫 audit（不含明碼）。
"""

import json
import threading
from collections.abc import Iterator
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from app.api.deps import CurrentStaff
from app.core.config import get_settings
from app.core.crypto import LABEL_HMAC_BINDING_CODE, derive_key, keyed_hash
from app.core.errors import AppError
from app.core.request_meta import RequestMeta
from app.models.account import StaffUser
from app.models.audit import AuditLog
from app.models.parents import Guardian, ParentBindingCode
from app.services import binding_code_service
from app.services.binding_code_service import (
    CODE_ALPHABET,
    CODE_LENGTH,
    CODE_TTL,
    IssuedBindingCode,
    claim,
    generate,
    hash_code,
    normalize_code,
)
from tests.integration.db.conftest import connect_owner
from tests.support.factories import make_guardian, make_parent, make_staff, make_student
from tests.support.fake_clock import FakeClock

_META = RequestMeta(ip="203.0.113.5", user_agent="pytest", request_id=None)


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
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


def current_staff_of(staff: StaffUser) -> CurrentStaff:
    return CurrentStaff(
        id=staff.id,
        username=staff.username,
        display_name=staff.display_name,
        role_id=staff.role.id,
        role_code=staff.role.code,
        role_name=staff.role.name,
        permissions=frozenset(staff.role.permissions),
        must_change_password=staff.must_change_password,
        token_version=staff.token_version,
    )


@pytest.fixture
def actor(db_session: Session) -> CurrentStaff:
    return current_staff_of(make_staff(db_session, permissions=["guardians:write"]))


def _generate(
    db_session: Session, guardian_id: UUID, actor: CurrentStaff, fake_clock: FakeClock
) -> IssuedBindingCode:
    return generate(db_session, guardian_id=guardian_id, actor=actor, meta=_META, clock=fake_clock)


def _codes(db_session: Session, guardian_id: UUID) -> list[ParentBindingCode]:
    return list(
        db_session.execute(
            select(ParentBindingCode)
            .where(ParentBindingCode.guardian_id == guardian_id)
            .order_by(ParentBindingCode.created_at)
        ).scalars()
    )


def test_generate_binding_code_format(
    db_session: Session, actor: CurrentStaff, fake_clock: FakeClock
) -> None:
    g = make_guardian(db_session, make_student(db_session))

    r = _generate(db_session, g.id, actor, fake_clock)

    assert CODE_LENGTH == 8
    assert timedelta(days=7) == CODE_TTL
    assert len(r.code) == 8
    assert set(r.code) <= set(CODE_ALPHABET)
    assert r.guardian_id == g.id
    assert r.expires_at == fake_clock.now() + timedelta(days=7)
    rows = _codes(db_session, g.id)
    assert len(rows) == 1
    assert rows[0].code_hash == hash_code(r.code)
    assert rows[0].code_hash == keyed_hash(LABEL_HMAC_BINDING_CODE, r.code)
    assert rows[0].expires_at == r.expires_at
    assert rows[0].used_at is None
    assert rows[0].created_by == actor.id
    raw_row = db_session.execute(
        text("select * from public.parent_binding_codes where id = :id"), {"id": rows[0].id}
    ).one()
    assert all(r.code not in str(value) for value in raw_row)


def test_generate_binding_code_unique_each_time(
    db_session: Session, actor: CurrentStaff, fake_clock: FakeClock
) -> None:
    g1 = make_guardian(db_session, make_student(db_session))
    g2 = make_guardian(db_session, make_student(db_session))

    codes = {_generate(db_session, g.id, actor, fake_clock).code for g in (g1, g2, g1)}
    assert len(codes) == 3


def test_generate_binding_code_invalidates_old(
    db_session: Session, actor: CurrentStaff, fake_clock: FakeClock
) -> None:
    g = make_guardian(db_session, make_student(db_session))
    first = _generate(db_session, g.id, actor, fake_clock)
    used = ParentBindingCode(
        created_at=fake_clock.now() - timedelta(days=2),
        guardian_id=g.id,
        code_hash=hash_code("USEDCODE"),
        expires_at=fake_clock.now() + timedelta(days=1),
        used_at=fake_clock.now() - timedelta(days=1),
        created_by=actor.id,
    )
    db_session.add(used)
    db_session.flush()
    # 另一位監護人的碼不受影響
    other = make_guardian(db_session, make_student(db_session))
    other_code = _generate(db_session, other.id, actor, fake_clock)

    fake_clock.advance(minutes=1)
    second = _generate(db_session, g.id, actor, fake_clock)

    rows = _codes(db_session, g.id)
    unused = [row for row in rows if row.used_at is None]
    assert len(unused) == 1
    assert unused[0].code_hash == hash_code(second.code)
    assert hash_code(first.code) not in {row.code_hash for row in rows}
    assert [row.code_hash for row in rows if row.used_at is not None] == [hash_code("USEDCODE")]
    assert [row.code_hash for row in _codes(db_session, other.id)] == [hash_code(other_code.code)]


def test_generate_binding_code_already_bound(
    db_session: Session, actor: CurrentStaff, fake_clock: FakeClock
) -> None:
    g = make_guardian(db_session, make_student(db_session), parent=make_parent(db_session))

    with pytest.raises(AppError) as excinfo:
        _generate(db_session, g.id, actor, fake_clock)

    assert excinfo.value.status == 409
    assert excinfo.value.code == "guardian_already_bound"
    assert _codes(db_session, g.id) == []


def test_generate_binding_code_archived(
    db_session: Session, actor: CurrentStaff, fake_clock: FakeClock
) -> None:
    archived_guardian = make_guardian(db_session, make_student(db_session), archived=True)
    with pytest.raises(AppError) as guardian_missing:
        _generate(db_session, archived_guardian.id, actor, fake_clock)
    assert guardian_missing.value.status == 404
    assert guardian_missing.value.code == "guardian_not_found"

    with pytest.raises(AppError) as unknown:
        _generate(db_session, uuid4(), actor, fake_clock)
    assert unknown.value.status == 404
    assert unknown.value.code == "guardian_not_found"

    g = make_guardian(db_session, make_student(db_session, archived=True))
    with pytest.raises(AppError) as student_archived:
        _generate(db_session, g.id, actor, fake_clock)
    assert student_archived.value.status == 409
    assert student_archived.value.code == "student_archived"
    assert _codes(db_session, g.id) == []


def test_generate_binding_code_audit(
    db_session: Session, actor: CurrentStaff, fake_clock: FakeClock
) -> None:
    g = make_guardian(db_session, make_student(db_session))

    r = _generate(db_session, g.id, actor, fake_clock)

    logs = list(
        db_session.execute(
            select(AuditLog).where(
                AuditLog.action == "guardian.binding_code_issue", AuditLog.entity_id == str(g.id)
            )
        ).scalars()
    )
    assert len(logs) == 1
    log = logs[0]
    assert log.actor_type == "staff"
    assert log.actor_id == actor.id
    assert log.entity_type == "guardian"
    assert log.before is None
    assert log.after == {"expires_at": r.expires_at.isoformat()}
    assert log.ip == "203.0.113.5"
    dumped = json.dumps(log.after)
    assert r.code not in dumped
    assert hash_code(r.code) not in dumped


def test_generate_binding_code_normalize() -> None:
    assert normalize_code(" abcd-2345 ") == "ABCD2345"
    assert normalize_code("AB CD 23 45") == "ABCD2345"
    assert normalize_code("abcd2345") == "ABCD2345"
    assert normalize_code("") == ""
    # hash 以正規化後的值計算，大小寫 / 連字號不同的輸入得到同一個 hash
    assert hash_code(normalize_code("abcd-2345")) == hash_code("ABCD2345")


def test_generate_binding_code_retries_on_hash_collision(
    db_session: Session,
    actor: CurrentStaff,
    fake_clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    g = make_guardian(db_session, make_student(db_session))
    other = make_guardian(db_session, make_student(db_session))
    existing = _generate(db_session, other.id, actor, fake_clock)

    # 前兩次抽到別人已存在的碼（code_hash unique 衝突），第三次才唯一
    drawn = iter([existing.code, existing.code, "ZZZZ2222"])
    monkeypatch.setattr(binding_code_service, "_random_code", lambda: next(drawn))

    r = _generate(db_session, g.id, actor, fake_clock)

    assert r.code == "ZZZZ2222"
    assert [row.code_hash for row in _codes(db_session, g.id)] == [hash_code("ZZZZ2222")]
    # 他人的碼未被動到；session 仍可用（savepoint 回滾，不是整筆 aborted）
    assert [row.code_hash for row in _codes(db_session, other.id)] == [hash_code(existing.code)]

    monkeypatch.setattr(binding_code_service, "_random_code", lambda: existing.code)
    with pytest.raises(AppError) as excinfo:
        _generate(db_session, g.id, actor, fake_clock)
    assert excinfo.value.status == 500
    assert excinfo.value.code == "binding_code_collision"


@pytest.fixture
def owner_cleanup_rows() -> Iterator[dict[str, list[UUID]]]:
    """committing 測試建立的列以 owner 連線依 id 刪除；排在 committing_db_session 之前。"""
    ids: dict[str, list[UUID]] = {"guardians": [], "students": [], "staff": [], "roles": []}
    yield ids
    with connect_owner() as conn:
        conn.execute("set lock_timeout = '5s'")
        for guardian_id in ids["guardians"]:
            conn.execute(
                "delete from public.parent_binding_codes where guardian_id = %s", (guardian_id,)
            )
            conn.execute("delete from public.guardians where id = %s", (guardian_id,))
        for student_id in ids["students"]:
            conn.execute("delete from public.students where id = %s", (student_id,))
        for staff_id in ids["staff"]:
            conn.execute("delete from public.staff_users where id = %s", (staff_id,))
        for role_id in ids["roles"]:
            conn.execute("delete from public.roles where id = %s", (role_id,))
        conn.commit()


@pytest.mark.cleanup_tables("audit_logs")
def test_generate_binding_code_concurrent_same_guardian(
    owner_cleanup_rows: dict[str, list[UUID]],
    committing_db_session: Session,
    db_engine: Engine,
    fake_clock: FakeClock,
) -> None:
    """同一 guardian 併發 generate：後到者等前者 commit 後才執行，最後只剩一筆有效碼。"""
    session = committing_db_session
    staff = make_staff(session, permissions=["guardians:write"])
    student = make_student(session)
    g = make_guardian(session, student)
    session.commit()
    owner_cleanup_rows["guardians"].append(g.id)
    owner_cleanup_rows["students"].append(student.id)
    owner_cleanup_rows["staff"].append(staff.id)
    owner_cleanup_rows["roles"].append(staff.role.id)
    actor = current_staff_of(staff)

    a_generated = threading.Event()
    release_a = threading.Event()
    b_done = threading.Event()
    results: dict[str, IssuedBindingCode] = {}
    errors: list[BaseException] = []

    def worker_a() -> None:
        sa = Session(bind=db_engine)
        try:
            results["a"] = _generate(sa, g.id, actor, fake_clock)
            a_generated.set()
            release_a.wait(timeout=10)
            sa.commit()
        except BaseException as exc:
            errors.append(exc)
            a_generated.set()
        finally:
            sa.close()

    def worker_b() -> None:
        sb = Session(bind=db_engine)
        try:
            a_generated.wait(timeout=10)
            results["b"] = _generate(sb, g.id, actor, fake_clock)
            sb.commit()
        except BaseException as exc:
            errors.append(exc)
        finally:
            sb.close()
            b_done.set()

    threads = [threading.Thread(target=worker_a), threading.Thread(target=worker_b)]
    for t in threads:
        t.start()
    try:
        assert a_generated.wait(timeout=10)
        # A 尚未 commit：B 必須被 guardian 列鎖擋住
        assert not b_done.wait(timeout=0.5)
    finally:
        release_a.set()
        for t in threads:
            t.join(timeout=10)
    assert errors == []
    assert b_done.is_set()
    assert results["a"].code != results["b"].code

    session.expire_all()
    rows = _codes(session, g.id)
    unused = [row for row in rows if row.used_at is None]
    assert len(unused) == 1
    assert unused[0].code_hash == hash_code(results["b"].code)


# --- BACKEND-055：claim ---------------------------------------------------------------------


def _claim(db_session: Session, raw_code: str, parent_id: UUID, fake_clock: FakeClock) -> Guardian:
    return claim(db_session, raw_code=raw_code, parent_account_id=parent_id, clock=fake_clock)


def _code_row(db_session: Session, code: str) -> ParentBindingCode:
    db_session.expire_all()
    return db_session.execute(
        select(ParentBindingCode).where(ParentBindingCode.code_hash == hash_code(code))
    ).scalar_one()


def _assert_400(exc: AppError, code: str) -> None:
    assert exc.status == 400
    assert exc.code == code


def test_claim_binding_code_success(
    db_session: Session, actor: CurrentStaff, fake_clock: FakeClock
) -> None:
    student = make_student(db_session, name="王小明")
    g = make_guardian(db_session, student)
    p = make_parent(db_session)
    issued = _generate(db_session, g.id, actor, fake_clock)
    fake_clock.advance(hours=1)
    raw = f" {issued.code[:4].lower()}-{issued.code[4:].lower()} "

    guardian = _claim(db_session, raw, p.id, fake_clock)

    assert guardian.id == g.id
    assert guardian.parent_account_id == p.id
    assert guardian.student.id == student.id
    assert guardian.student.name == "王小明"
    assert _code_row(db_session, issued.code).used_at == fake_clock.now()
    db_session.expire_all()
    assert db_session.get(Guardian, g.id).parent_account_id == p.id  # type: ignore[union-attr]


def test_claim_binding_code_invalid(
    db_session: Session, actor: CurrentStaff, fake_clock: FakeClock
) -> None:
    p = make_parent(db_session)
    for raw in ("ZZZZ", "", "ABCD-234", "ABCD23450", "ABCD2345!", "ABCD1234", "ABCD0OI2"):
        with pytest.raises(AppError) as excinfo:
            _claim(db_session, raw, p.id, fake_clock)
        _assert_400(excinfo.value, "binding_code_invalid")
    # 格式正確但不存在
    with pytest.raises(AppError) as missing:
        _claim(db_session, "ZZZZ2222", p.id, fake_clock)
    _assert_400(missing.value, "binding_code_invalid")


def test_claim_binding_code_archived_targets(
    db_session: Session, actor: CurrentStaff, fake_clock: FakeClock
) -> None:
    """產碼後 guardian 或學生被封存：碼視為無效（不洩漏原因）。"""
    p = make_parent(db_session)
    g = make_guardian(db_session, make_student(db_session))
    issued = _generate(db_session, g.id, actor, fake_clock)
    g.archived_at = fake_clock.now()
    db_session.flush()
    with pytest.raises(AppError) as excinfo:
        _claim(db_session, issued.code, p.id, fake_clock)
    _assert_400(excinfo.value, "binding_code_invalid")

    student = make_student(db_session)
    g2 = make_guardian(db_session, student)
    issued2 = _generate(db_session, g2.id, actor, fake_clock)
    student.archived_at = fake_clock.now()
    db_session.flush()
    with pytest.raises(AppError) as student_archived:
        _claim(db_session, issued2.code, p.id, fake_clock)
    _assert_400(student_archived.value, "binding_code_invalid")


def test_claim_binding_code_expired_and_used(
    db_session: Session, actor: CurrentStaff, fake_clock: FakeClock
) -> None:
    p = make_parent(db_session)
    q = make_parent(db_session)
    expired_guardian = make_guardian(db_session, make_student(db_session))
    expired = _generate(db_session, expired_guardian.id, actor, fake_clock)
    used_guardian = make_guardian(db_session, make_student(db_session))
    used = _generate(db_session, used_guardian.id, actor, fake_clock)
    assert _claim(db_session, used.code, p.id, fake_clock).parent_account_id == p.id

    with pytest.raises(AppError) as reused:
        _claim(db_session, used.code, q.id, fake_clock)
    _assert_400(reused.value, "binding_code_used")
    # 已綁定的 guardian 不變
    assert _code_row(db_session, used.code).used_at == fake_clock.now()

    fake_clock.advance(days=7, seconds=1)
    with pytest.raises(AppError) as late:
        _claim(db_session, expired.code, p.id, fake_clock)
    _assert_400(late.value, "binding_code_expired")
    assert _code_row(db_session, expired.code).used_at is None
    # 過期且已用：過期優先
    with pytest.raises(AppError) as both:
        _claim(db_session, used.code, q.id, fake_clock)
    _assert_400(both.value, "binding_code_expired")


def test_claim_binding_code_guardian_bound_by_other(
    db_session: Session, actor: CurrentStaff, fake_clock: FakeClock
) -> None:
    p1 = make_parent(db_session)
    p2 = make_parent(db_session)
    g = make_guardian(db_session, make_student(db_session))
    issued = _generate(db_session, g.id, actor, fake_clock)
    # 產碼後才被另一位家長綁走（例如另一個碼）
    g.parent_account_id = p1.id
    db_session.flush()
    db_session.commit()  # 釋放 savepoint，之後的 rollback 只退回 claim 的變更

    with pytest.raises(AppError) as excinfo:
        _claim(db_session, issued.code, p2.id, fake_clock)

    assert excinfo.value.status == 409
    assert excinfo.value.code == "guardian_already_bound"
    db_session.rollback()
    assert _code_row(db_session, issued.code).used_at is None
    assert db_session.get(Guardian, g.id).parent_account_id == p1.id  # type: ignore[union-attr]
    # 同一位家長再 claim 自己已綁的 guardian：允許（冪等）
    assert _claim(db_session, issued.code, p1.id, fake_clock).parent_account_id == p1.id


def test_claim_binding_code_duplicate_student(
    db_session: Session, actor: CurrentStaff, fake_clock: FakeClock
) -> None:
    p = make_parent(db_session)
    student = make_student(db_session)
    make_guardian(db_session, student, parent=p, name="王媽媽")
    g2 = make_guardian(db_session, student, name="王爸爸", relation="father")
    issued = _generate(db_session, g2.id, actor, fake_clock)
    db_session.commit()

    with pytest.raises(AppError) as excinfo:
        _claim(db_session, issued.code, p.id, fake_clock)

    assert excinfo.value.status == 409
    assert excinfo.value.code == "already_bound_to_student"
    # savepoint 回滾後 session 仍可用；呼叫端 rollback 後碼未被消耗
    db_session.rollback()
    assert _code_row(db_session, issued.code).used_at is None
    assert db_session.get(Guardian, g2.id).parent_account_id is None  # type: ignore[union-attr]


@pytest.fixture
def owner_cleanup_claim_rows() -> Iterator[dict[str, list[UUID]]]:
    """並發 claim 測試的列以 owner 連線依 id 刪除；排在 committing_db_session 之前。"""
    ids: dict[str, list[UUID]] = {
        "guardians": [],
        "students": [],
        "parents": [],
        "staff": [],
        "roles": [],
    }
    yield ids
    with connect_owner() as conn:
        conn.execute("set lock_timeout = '5s'")
        for guardian_id in ids["guardians"]:
            conn.execute(
                "delete from public.parent_binding_codes where guardian_id = %s", (guardian_id,)
            )
            conn.execute("delete from public.guardians where id = %s", (guardian_id,))
        for student_id in ids["students"]:
            conn.execute("delete from public.students where id = %s", (student_id,))
        for parent_id in ids["parents"]:
            conn.execute("delete from public.parent_accounts where id = %s", (parent_id,))
        for staff_id in ids["staff"]:
            conn.execute("delete from public.staff_users where id = %s", (staff_id,))
        for role_id in ids["roles"]:
            conn.execute("delete from public.roles where id = %s", (role_id,))
        conn.commit()


@pytest.mark.cleanup_tables("audit_logs")
def test_claim_binding_code_concurrent(
    owner_cleanup_claim_rows: dict[str, list[UUID]],
    committing_db_session: Session,
    db_engine: Engine,
    fake_clock: FakeClock,
) -> None:
    """兩條連線同時兌換同一碼：恰一條成功，另一條 400 binding_code_used。"""
    session = committing_db_session
    staff = make_staff(session, permissions=["guardians:write"])
    student = make_student(session)
    g = make_guardian(session, student)
    p1 = make_parent(session)
    p2 = make_parent(session)
    issued = _generate(session, g.id, current_staff_of(staff), fake_clock)
    session.commit()
    owner_cleanup_claim_rows["guardians"].append(g.id)
    owner_cleanup_claim_rows["students"].append(student.id)
    owner_cleanup_claim_rows["parents"] += [p1.id, p2.id]
    owner_cleanup_claim_rows["staff"].append(staff.id)
    owner_cleanup_claim_rows["roles"].append(staff.role.id)

    barrier = threading.Barrier(2)
    outcomes: dict[UUID, str] = {}
    unexpected: list[BaseException] = []

    def worker(parent_id: UUID) -> None:
        s = Session(bind=db_engine)
        try:
            barrier.wait(timeout=10)
            try:
                _claim(s, issued.code, parent_id, fake_clock)
                s.commit()
                outcomes[parent_id] = "ok"
            except AppError as exc:
                s.rollback()
                outcomes[parent_id] = exc.code
        except BaseException as exc:
            unexpected.append(exc)
        finally:
            s.close()

    threads = [threading.Thread(target=worker, args=(pid,)) for pid in (p1.id, p2.id)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    assert unexpected == []
    assert sorted(outcomes.values()) == ["binding_code_used", "ok"]
    winner = next(pid for pid, outcome in outcomes.items() if outcome == "ok")
    session.expire_all()
    assert session.get(Guardian, g.id).parent_account_id == winner  # type: ignore[union-attr]
    assert _code_row(session, issued.code).used_at == fake_clock.now()
