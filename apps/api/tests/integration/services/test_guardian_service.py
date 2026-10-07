"""BACKEND-168：app/services/guardian_service.py（list_for_student、to_guardian_out）。
BACKEND-170：update_guardian（部分更新、主要聯絡人互斥切換、封存 / 不存在 404）。
BACKEND-171：archive_guardian（軟刪除、作廢未使用綁定碼、已綁定寫 guardian.unbind 稽核）。
BACKEND-172：unbind_guardian（解除綁定、未綁定 409、不影響家長的其他綁定）。
BACKEND-549：170 / 171 / 172 共用 ``_lock_guardian_for_write`` 的並發回歸（學生列鎖序列化同一學生的
監護人異動；guardian 列 FOR UPDATE + populate_existing 重讀）。"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session

from app.api.deps import CurrentStaff
from app.core.config import get_settings
from app.core.crypto import derive_key
from app.core.errors import AppError
from app.core.request_meta import RequestMeta
from app.models.account import StaffUser
from app.models.audit import AuditLog
from app.models.parents import Guardian, ParentBindingCode
from app.schemas.guardians import GuardianCreateIn, GuardianOut, GuardianUpdateIn
from app.services.binding_code_service import claim, generate
from app.services.guardian_service import (
    archive_guardian,
    create_guardian,
    list_for_student,
    to_guardian_out,
    unbind_guardian,
    update_guardian,
)
from app.services.parent_scope import get_parent_student_ids
from tests.integration.db.conftest import connect_owner
from tests.support.factories import make_guardian, make_parent, make_staff, make_student
from tests.support.fake_clock import FakeClock

_NOW = datetime(2026, 9, 10, 4, 0, tzinfo=UTC)
_BASE = datetime(2026, 9, 1, tzinfo=UTC)
_META = RequestMeta(ip="127.0.0.1", user_agent="pytest", request_id="req-1")


def _clock() -> FakeClock:
    return FakeClock(_NOW)


def _stamp(db: Session, *guardians: Guardian) -> None:
    """同一交易內 created_at 都是 now()，明確指定以固定「建立先後」。"""
    for index, guardian in enumerate(guardians):
        guardian.created_at = _BASE + timedelta(minutes=index)
    db.flush()


def _code(
    db: Session,
    guardian: Guardian,
    staff_id: object,
    *,
    expires_at: datetime,
    used: bool = False,
) -> None:
    db.add(
        ParentBindingCode(
            guardian_id=guardian.id,
            code_hash=uuid4().hex + uuid4().hex,
            expires_at=expires_at,
            used_at=_NOW if used else None,
            created_by=staff_id,
            # CHECK：expires_at 必須晚於 created_at；過期碼的 created_at 要更早
            created_at=_NOW - timedelta(days=5),
        )
    )
    db.flush()


def test_list_guardians_order_and_archived(db_session: Session) -> None:
    student = make_student(db_session)
    first = make_guardian(db_session, student, name="爸爸", relation="father")
    primary = make_guardian(db_session, student, name="媽媽", is_primary=True)
    gone = make_guardian(db_session, student, name="奶奶", relation="grandparent", archived=True)
    later = make_guardian(db_session, student, name="阿姨", relation="other")
    _stamp(db_session, first, primary, gone, later)

    result = list_for_student(db_session, student.id, clock=_clock())

    assert [g.name for g in result] == ["媽媽", "爸爸", "阿姨"]
    assert result[0].is_primary is True
    assert {g.student_id for g in result} == {student.id}


def test_list_guardians_binding_status(db_session: Session) -> None:
    staff = make_staff(db_session)
    student = make_student(db_session)
    parent = make_parent(db_session, display_name="王媽媽")
    g1 = make_guardian(db_session, student, parent=parent, name="g1", is_primary=True)
    g2 = make_guardian(db_session, student, name="g2")
    g3 = make_guardian(db_session, student, name="g3", relation="father")
    g4 = make_guardian(db_session, student, name="g4", relation="other")
    _stamp(db_session, g1, g2, g3, g4)
    expires = _NOW + timedelta(days=3)
    _code(db_session, g2, staff.id, expires_at=_NOW + timedelta(days=1))
    _code(db_session, g2, staff.id, expires_at=expires)
    _code(db_session, g3, staff.id, expires_at=_NOW - timedelta(seconds=1))
    _code(db_session, g4, staff.id, expires_at=_NOW + timedelta(days=2), used=True)

    result = list_for_student(db_session, student.id, clock=_clock())

    assert [g.binding.status for g in result] == ["bound", "code_issued", "unbound", "unbound"]
    assert result[0].binding.parent_display_name == "王媽媽"
    assert result[0].binding.code_expires_at is None
    assert result[1].binding.code_expires_at == expires  # 最晚到期的未使用碼
    assert result[1].binding.parent_display_name is None
    assert result[2].binding.code_expires_at is None


def test_list_guardians_bound_ignores_codes(db_session: Session) -> None:
    staff = make_staff(db_session)
    student = make_student(db_session)
    guardian = make_guardian(db_session, student, parent=make_parent(db_session))
    _code(db_session, guardian, staff.id, expires_at=_NOW + timedelta(days=1))

    (only,) = list_for_student(db_session, student.id, clock=_clock())

    assert only.binding.status == "bound"
    assert only.binding.code_expires_at is None


def test_list_guardians_archived_student_visible(db_session: Session) -> None:
    student = make_student(db_session, archived=True)
    make_guardian(db_session, student)

    assert len(list_for_student(db_session, student.id, clock=_clock())) == 1


def test_list_guardians_student_not_found(db_session: Session) -> None:
    with pytest.raises(AppError) as exc:
        list_for_student(db_session, uuid4(), clock=_clock())

    assert (exc.value.status, exc.value.code) == (404, "student_not_found")


def test_list_guardians_to_guardian_out(db_session: Session) -> None:
    student = make_student(db_session)
    guardian = make_guardian(db_session, student, name="爸爸", relation="father")
    expires = _NOW + timedelta(days=1)

    unbound = to_guardian_out(guardian, None)
    issued = to_guardian_out(guardian, expires)

    assert unbound.binding.status == "unbound"
    assert issued.binding.status == "code_issued"
    assert issued.binding.code_expires_at == expires
    assert (issued.id, issued.name, issued.relation) == (guardian.id, "爸爸", "father")


def test_create_guardian_success(db_session: Session) -> None:
    student = make_student(db_session)

    out = create_guardian(
        db_session,
        student.id,
        GuardianCreateIn(name="王爸爸", relation="father", phone="0912-000-123"),
        clock=_clock(),
    )

    assert out.binding.status == "unbound"
    assert out.is_primary is False
    assert (out.name, out.relation, out.phone) == ("王爸爸", "father", "0912-000-123")
    assert (out.can_pickup, out.receives_notifications) == (True, True)
    assert out.student_id == student.id
    stored = db_session.execute(select(Guardian).where(Guardian.id == out.id)).scalar_one()
    assert stored.parent_account_id is None
    assert stored.archived_at is None


def test_create_guardian_primary_switch(db_session: Session) -> None:
    student = make_student(db_session)
    g1 = make_guardian(db_session, student, name="王媽媽", is_primary=True)
    archived_primary = make_guardian(
        db_session, student, name="已封存", relation="other", archived=True
    )

    out = create_guardian(
        db_session,
        student.id,
        GuardianCreateIn(name="王爸爸", relation="father", is_primary=True),
        clock=_clock(),
    )

    db_session.refresh(g1)
    db_session.refresh(archived_primary)
    assert g1.is_primary is False
    assert out.is_primary is True
    primaries = db_session.execute(
        select(func.count())
        .select_from(Guardian)
        .where(
            Guardian.student_id == student.id, Guardian.is_primary, Guardian.archived_at.is_(None)
        )
    ).scalar_one()
    assert primaries == 1


def test_create_guardian_non_primary_keeps_existing_primary(db_session: Session) -> None:
    student = make_student(db_session)
    g1 = make_guardian(db_session, student, name="王媽媽", is_primary=True)

    create_guardian(
        db_session, student.id, GuardianCreateIn(name="王爸爸", relation="father"), clock=_clock()
    )

    db_session.refresh(g1)
    assert g1.is_primary is True


def test_create_guardian_archived_student(db_session: Session) -> None:
    student = make_student(db_session, archived=True)

    with pytest.raises(AppError) as exc:
        create_guardian(
            db_session,
            student.id,
            GuardianCreateIn(name="王爸爸", relation="father"),
            clock=_clock(),
        )

    assert (exc.value.status, exc.value.code) == (409, "student_archived")


def test_create_guardian_student_not_found(db_session: Session) -> None:
    with pytest.raises(AppError) as exc:
        create_guardian(
            db_session, uuid4(), GuardianCreateIn(name="王爸爸", relation="father"), clock=_clock()
        )

    assert (exc.value.status, exc.value.code) == (404, "student_not_found")


# --- BACKEND-170：update_guardian ----------------------------------------------------------------


def _actor() -> CurrentStaff:
    return CurrentStaff(
        id=uuid4(),
        username="clerk",
        display_name="陳行政",
        role_id=uuid4(),
        role_code="clerk",
        role_name="行政",
        permissions=frozenset({"students:write"}),
        must_change_password=False,
        token_version=0,
    )


def _unused_codes(db: Session, guardian_id: object) -> int:
    return db.execute(
        select(func.count())
        .select_from(ParentBindingCode)
        .where(ParentBindingCode.guardian_id == guardian_id, ParentBindingCode.used_at.is_(None))
    ).scalar_one()


def _unbind_audits(db: Session, guardian_id: object) -> list[AuditLog]:
    return list(
        db.execute(
            select(AuditLog).where(
                AuditLog.action == "guardian.unbind", AuditLog.entity_id == str(guardian_id)
            )
        ).scalars()
    )


def test_update_guardian_partial(db_session: Session) -> None:
    staff = make_staff(db_session)
    student = make_student(db_session)
    guardian = make_guardian(db_session, student, name="王媽媽", is_primary=True, can_pickup=False)
    guardian.phone = "0912-000-001"
    db_session.flush()
    expires = _NOW + timedelta(days=2)
    _code(db_session, guardian, staff.id, expires_at=expires)

    out = update_guardian(
        db_session, guardian.id, GuardianUpdateIn(phone="0912-000-002"), clock=_clock()
    )

    assert out.id == guardian.id
    assert out.phone == "0912-000-002"
    assert (out.name, out.relation, out.is_primary, out.can_pickup) == (
        "王媽媽",
        "mother",
        True,
        False,
    )
    assert out.receives_notifications is True
    assert out.binding.status == "code_issued"
    assert out.binding.code_expires_at == expires
    db_session.refresh(guardian)
    assert (guardian.phone, guardian.name, guardian.is_primary) == ("0912-000-002", "王媽媽", True)
    # 清除電話（nullable）
    cleared = update_guardian(
        db_session, guardian.id, GuardianUpdateIn.model_validate({"phone": None}), clock=_clock()
    )
    assert cleared.phone is None


def test_update_guardian_primary_switch(db_session: Session) -> None:
    student = make_student(db_session)
    g1 = make_guardian(db_session, student, name="王媽媽", is_primary=True)
    g2 = make_guardian(db_session, student, name="王爸爸", relation="father")
    other_student_primary = make_guardian(db_session, make_student(db_session), is_primary=True)

    out = update_guardian(db_session, g2.id, GuardianUpdateIn(is_primary=True), clock=_clock())

    db_session.refresh(g1)
    db_session.refresh(g2)
    db_session.refresh(other_student_primary)
    assert out.is_primary is True
    assert (g1.is_primary, g2.is_primary) == (False, True)
    assert other_student_primary.is_primary is True  # 別的學生不受影響
    # 允許學生沒有主要聯絡人
    update_guardian(db_session, g2.id, GuardianUpdateIn(is_primary=False), clock=_clock())
    db_session.refresh(g1)
    db_session.refresh(g2)
    assert (g1.is_primary, g2.is_primary) == (False, False)
    # 自己已是 primary 再設 True 不會撞 uq_guardians_one_primary
    update_guardian(db_session, g1.id, GuardianUpdateIn(is_primary=True), clock=_clock())
    again = update_guardian(db_session, g1.id, GuardianUpdateIn(is_primary=True), clock=_clock())
    assert again.is_primary is True


def test_update_guardian_not_found(db_session: Session) -> None:
    student = make_student(db_session)
    archived = make_guardian(db_session, student, name="已封存", archived=True)

    with pytest.raises(AppError) as gone:
        update_guardian(db_session, archived.id, GuardianUpdateIn(name="新名"), clock=_clock())
    with pytest.raises(AppError) as missing:
        update_guardian(db_session, uuid4(), GuardianUpdateIn(name="新名"), clock=_clock())

    assert (gone.value.status, gone.value.code) == (404, "guardian_not_found")
    assert (missing.value.status, missing.value.code) == (404, "guardian_not_found")
    db_session.refresh(archived)
    assert archived.name == "已封存"


# --- BACKEND-171：archive_guardian ---------------------------------------------------------------


def test_archive_guardian_success(db_session: Session) -> None:
    staff = make_staff(db_session)
    student = make_student(db_session)
    guardian = make_guardian(db_session, student, name="王媽媽", is_primary=True)
    other = make_guardian(db_session, student, name="王爸爸", relation="father")
    _code(db_session, guardian, staff.id, expires_at=_NOW + timedelta(days=1))
    _code(db_session, guardian, staff.id, expires_at=_NOW + timedelta(days=2), used=True)
    _code(db_session, other, staff.id, expires_at=_NOW + timedelta(days=1))

    archive_guardian(db_session, guardian.id, actor=_actor(), meta=_META, clock=_clock())

    assert [g.id for g in list_for_student(db_session, student.id, clock=_clock())] == [other.id]
    db_session.refresh(guardian)
    assert guardian.archived_at == _NOW
    assert guardian.is_primary is False
    assert _unused_codes(db_session, guardian.id) == 0
    used = db_session.execute(
        select(func.count())
        .select_from(ParentBindingCode)
        .where(ParentBindingCode.guardian_id == guardian.id)
    ).scalar_one()
    assert used == 1  # 已使用的碼保留
    assert _unused_codes(db_session, other.id) == 1
    # 未綁定：不寫 guardian.unbind 稽核
    assert _unbind_audits(db_session, guardian.id) == []
    # 封存後主要聯絡人可讓給別人
    update_guardian(db_session, other.id, GuardianUpdateIn(is_primary=True), clock=_clock())


def test_archive_guardian_bound_audit_and_scope(db_session: Session) -> None:
    student = make_student(db_session)
    parent = make_parent(db_session)
    guardian = make_guardian(db_session, student, parent=parent, name="王媽媽")
    actor = _actor()
    assert get_parent_student_ids(db_session, parent.id) == [student.id]

    archive_guardian(db_session, guardian.id, actor=actor, meta=_META, clock=_clock())

    db_session.refresh(guardian)
    assert guardian.parent_account_id == parent.id  # 歷史保留
    assert guardian.archived_at == _NOW
    assert get_parent_student_ids(db_session, parent.id) == []
    audits = _unbind_audits(db_session, guardian.id)
    assert len(audits) == 1
    assert audits[0].before == {"parent_account_id": str(parent.id)}
    assert audits[0].after == {"reason": "archived"}
    assert (audits[0].actor_type, audits[0].actor_id, audits[0].entity_type) == (
        "staff",
        actor.id,
        "guardian",
    )
    assert audits[0].ip == "127.0.0.1"


def test_archive_guardian_not_found(db_session: Session) -> None:
    student = make_student(db_session)
    archived = make_guardian(db_session, student, archived=True)

    with pytest.raises(AppError) as missing:
        archive_guardian(db_session, uuid4(), actor=_actor(), meta=_META, clock=_clock())
    with pytest.raises(AppError) as twice:
        archive_guardian(db_session, archived.id, actor=_actor(), meta=_META, clock=_clock())

    assert (missing.value.status, missing.value.code) == (404, "guardian_not_found")
    assert (twice.value.status, twice.value.code) == (404, "guardian_not_found")


# --- BACKEND-172：unbind_guardian ----------------------------------------------------------------


def test_unbind_guardian_success(db_session: Session) -> None:
    staff = make_staff(db_session)
    student = make_student(db_session)
    parent = make_parent(db_session, display_name="王媽媽")
    guardian = make_guardian(db_session, student, parent=parent, name="王媽媽", is_primary=True)
    _code(db_session, guardian, staff.id, expires_at=_NOW + timedelta(days=1))
    actor = _actor()

    out = unbind_guardian(db_session, guardian.id, actor=actor, meta=_META, clock=_clock())

    assert out.id == guardian.id
    assert out.binding.status == "unbound"
    assert out.binding.parent_display_name is None
    assert out.is_primary is True  # 其他欄位不變
    db_session.refresh(guardian)
    assert guardian.parent_account_id is None
    assert guardian.archived_at is None
    assert _unused_codes(db_session, guardian.id) == 0
    assert get_parent_student_ids(db_session, parent.id) == []
    audits = _unbind_audits(db_session, guardian.id)
    assert len(audits) == 1
    assert audits[0].before == {"parent_account_id": str(parent.id)}
    assert (audits[0].actor_type, audits[0].actor_id) == ("staff", actor.id)
    (listed,) = list_for_student(db_session, student.id, clock=_clock())
    assert listed.binding.status == "unbound"


def test_unbind_guardian_not_bound(db_session: Session) -> None:
    student = make_student(db_session)
    guardian = make_guardian(db_session, student)
    archived_bound = make_guardian(
        db_session, student, parent=make_parent(db_session), relation="father", archived=True
    )

    with pytest.raises(AppError) as exc:
        unbind_guardian(db_session, guardian.id, actor=_actor(), meta=_META, clock=_clock())
    with pytest.raises(AppError) as gone:
        unbind_guardian(db_session, archived_bound.id, actor=_actor(), meta=_META, clock=_clock())
    with pytest.raises(AppError) as missing:
        unbind_guardian(db_session, uuid4(), actor=_actor(), meta=_META, clock=_clock())

    assert (exc.value.status, exc.value.code) == (409, "guardian_not_bound")
    assert (gone.value.status, gone.value.code) == (404, "guardian_not_found")
    assert (missing.value.status, missing.value.code) == (404, "guardian_not_found")
    assert _unbind_audits(db_session, guardian.id) == []


def test_unbind_guardian_keeps_other_binding(db_session: Session) -> None:
    parent = make_parent(db_session)
    student_a = make_student(db_session, name="王小明")
    student_b = make_student(db_session, name="王小華")
    guardian_a = make_guardian(db_session, student_a, parent=parent)
    make_guardian(db_session, student_b, parent=parent)
    assert set(get_parent_student_ids(db_session, parent.id)) == {student_a.id, student_b.id}

    unbind_guardian(db_session, guardian_a.id, actor=_actor(), meta=_META, clock=_clock())

    assert get_parent_student_ids(db_session, parent.id) == [student_b.id]
    # 家長帳號本身保留
    assert parent.status == "active"


# --- BACKEND-549：並發回歸（學生列鎖與 FOR UPDATE 重讀） ------------------------------------------
# 兩條 app_backend 連線 + threading 阻塞模式：一邊持鎖未 commit，另一邊在 thread 呼叫 service，
# 斷言「放鎖前 0.5 秒內未完成、放鎖後完成且結果正確」。lock_timeout 一律 SET LOCAL（連線會回 pool，
# session 級設定會污染之後借到該連線的測試）。

_LOCK_TIMEOUT = "set local lock_timeout = '15s'"
_CONCURRENT_PHONE = "0912-000-456"


@pytest.fixture
def secret_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """binding_code_service 的 hash_code 需要 APP_SECRET_KEY（HMAC）。"""
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


@pytest.fixture
def owner_cleanup_guardians() -> Iterator[dict[str, list[UUID]]]:
    """committing 測試建立的列以 owner 連線依 id 刪除（稽核 → 綁定碼 → 監護人 → 學生 → 家長 →
    員工）；排在 committing_db_session 之前。只用 seed 角色，不往 roles 寫列。"""
    ids: dict[str, list[UUID]] = {"guardians": [], "students": [], "parents": [], "staff": []}
    yield ids
    with connect_owner() as conn:
        conn.execute("set lock_timeout = '5s'")
        for guardian_id in ids["guardians"]:
            conn.execute(
                "delete from public.audit_logs where entity_type = 'guardian' and entity_id = %s",
                (str(guardian_id),),
            )
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
        conn.commit()


def _call_in_thread(
    sb: Session, call: Callable[[Session], object]
) -> tuple[threading.Thread, threading.Event, dict[str, object]]:
    """在 thread 以 ``sb`` 執行 ``call``：成功 → commit 並記回傳值；AppError → 記 (status, code)，
    交易留給呼叫端檢視 ORM 狀態後再關閉；其他例外原樣記在 ``error``。"""
    done = threading.Event()
    outcome: dict[str, object] = {}

    def worker() -> None:
        try:
            try:
                outcome["result"] = call(sb)
                sb.commit()
            except AppError as exc:
                outcome["result"] = (exc.status, exc.code)
        except BaseException as exc:
            outcome["error"] = exc
        finally:
            done.set()

    thread = threading.Thread(target=worker)
    thread.start()
    return thread, done, outcome


def _primary_flags(db: Session, *guardian_ids: UUID) -> dict[UUID, bool]:
    rows = db.execute(select(Guardian.id, Guardian.is_primary).where(Guardian.id.in_(guardian_ids)))
    return {guardian_id: is_primary for guardian_id, is_primary in rows}


def _staff_actor(staff: StaffUser) -> CurrentStaff:
    return CurrentStaff(
        id=staff.id,
        username=staff.username,
        display_name=staff.display_name,
        role_id=staff.role.id,
        role_code=staff.role.code,
        role_name=staff.role.name,
        permissions=frozenset(staff.role.permissions),
        must_change_password=False,
        token_version=staff.token_version,
    )


@pytest.mark.cleanup_tables("class_staff")
@pytest.mark.parametrize("method", ["update", "archive", "unbind"])
def test_guardian_concurrent_write_methods_block_on_student_row_lock(
    method: str,
    owner_cleanup_guardians: dict[str, list[UUID]],
    committing_db_session: Session,
    db_engine: Engine,
) -> None:
    """外部連線對學生列持 FOR UPDATE（例如同一學生的 create_guardian 進行中）：三個寫入方法都要在
    ``_lock_guardian_for_write`` 的學生列鎖排隊——放鎖前 0.5 秒內未完成，放鎖後完成且結果正確。
    （沒有學生列鎖時 UPDATE guardians 不碰學生列，thread 會立刻完成。）"""
    db = committing_db_session
    student = make_student(db)
    parent = make_parent(db) if method == "unbind" else None
    guardian = make_guardian(db, student, parent=parent, name="王媽媽", is_primary=True)
    db.commit()
    owner_cleanup_guardians["guardians"].append(guardian.id)
    owner_cleanup_guardians["students"].append(student.id)
    if parent is not None:
        owner_cleanup_guardians["parents"].append(parent.id)
    sid, gid = student.id, guardian.id
    clock = _clock()

    def call(sb: Session) -> object:
        if method == "update":
            return update_guardian(sb, gid, GuardianUpdateIn(phone=_CONCURRENT_PHONE), clock=clock)
        if method == "archive":
            archive_guardian(sb, gid, actor=_actor(), meta=_META, clock=clock)
            return "archived"
        return unbind_guardian(sb, gid, actor=_actor(), meta=_META, clock=clock)

    holder = Session(bind=db_engine)
    sb = Session(bind=db_engine)
    try:
        holder.execute(text(_LOCK_TIMEOUT))
        holder.execute(text("select id from students where id = :id for update"), {"id": sid})
        sb.execute(text(_LOCK_TIMEOUT))
        thread, done, outcome = _call_in_thread(sb, call)
        try:
            assert not done.wait(timeout=0.5), outcome  # 學生列被鎖住：寫入方法必須排隊
        finally:
            holder.rollback()  # 放鎖
        thread.join(timeout=15)
        assert done.is_set(), "放鎖後 thread 仍未完成"
    finally:
        holder.close()
        sb.close()

    assert "error" not in outcome, outcome
    result = outcome["result"]
    with Session(bind=db_engine) as check:
        stored = check.get(Guardian, gid)
        assert stored is not None
        if method == "update":
            assert isinstance(result, GuardianOut)
            assert result.phone == _CONCURRENT_PHONE
            assert stored.phone == _CONCURRENT_PHONE
            assert stored.is_primary is True
        elif method == "archive":
            assert result == "archived"
            assert stored.archived_at == _NOW
            assert stored.is_primary is False
        else:
            assert isinstance(result, GuardianOut)
            assert result.binding.status == "unbound"
            assert stored.parent_account_id is None
            assert len(_unbind_audits(check, gid)) == 1


@pytest.mark.cleanup_tables("class_staff")
def test_guardian_concurrent_primary_switch_ends_with_single_primary(
    owner_cleanup_guardians: dict[str, list[UUID]],
    committing_db_session: Session,
    db_engine: Engine,
) -> None:
    """同一學生 G0 為主要聯絡人，兩位員工同時把 G1、G2 設成主要聯絡人：B 在學生列鎖排隊，A commit
    後 B 才查詢、看得到 A 剛升上去的 G1 並把它降回去 → 兩者都成功、最後恰 G2 一位 is_primary，
    不會撞 uq_guardians_one_primary。（沒有學生列鎖時 B 只擋在 G0 的列鎖，降級 UPDATE 的快照
    看不到 G1 的新值，flush 時撞唯一索引。）"""
    db = committing_db_session
    student = make_student(db)
    g0 = make_guardian(db, student, name="王媽媽", is_primary=True)
    g1 = make_guardian(db, student, name="王爸爸", relation="father")
    g2 = make_guardian(db, student, name="王奶奶", relation="grandparent")
    db.commit()
    owner_cleanup_guardians["guardians"] += [g0.id, g1.id, g2.id]
    owner_cleanup_guardians["students"].append(student.id)
    g0_id, g1_id, g2_id = g0.id, g1.id, g2.id
    clock = _clock()

    sa = Session(bind=db_engine)
    sb = Session(bind=db_engine)
    try:
        sa.execute(text(_LOCK_TIMEOUT))
        out_a = update_guardian(sa, g1_id, GuardianUpdateIn(is_primary=True), clock=clock)
        sb.execute(text(_LOCK_TIMEOUT))
        thread, done, outcome = _call_in_thread(
            sb, lambda s: update_guardian(s, g2_id, GuardianUpdateIn(is_primary=True), clock=clock)
        )
        try:
            assert not done.wait(timeout=0.5), outcome  # A 未 commit：B 在學生列鎖排隊
        finally:
            sa.commit()
        thread.join(timeout=15)
        assert done.is_set(), "A commit 後 thread 仍未完成"
    finally:
        sa.close()
        sb.close()

    assert out_a.is_primary is True
    assert "error" not in outcome, outcome
    out_b = outcome["result"]
    assert isinstance(out_b, GuardianOut), out_b
    assert out_b.is_primary is True
    with Session(bind=db_engine) as check:
        assert _primary_flags(check, g0_id, g1_id, g2_id) == {
            g0_id: False,
            g1_id: False,
            g2_id: True,
        }


@pytest.mark.cleanup_tables("class_staff")
def test_guardian_concurrent_update_after_archive_returns_404(
    owner_cleanup_guardians: dict[str, list[UUID]],
    committing_db_session: Session,
    db_engine: Engine,
) -> None:
    """B 事先載入並持有 G 的 ORM 物件（未封存）；A archive_guardian(G) 取得鎖未 commit → B
    update_guardian(G, is_primary=True) 在學生列鎖排隊 → A commit → B 的 FOR UPDATE 重讀以
    populate_existing 覆蓋 identity map 舊值、看到 archived_at → 404 guardian_not_found；G 維持
    A 寫入的 archived_at 與 is_primary=False。（沒有 populate_existing 時 B 讀到舊值，把已封存的
    G 設成主要聯絡人。）"""
    db = committing_db_session
    student = make_student(db)
    guardian = make_guardian(db, student, name="王媽媽")
    db.commit()
    owner_cleanup_guardians["guardians"].append(guardian.id)
    owner_cleanup_guardians["students"].append(student.id)
    gid = guardian.id
    clock = _clock()

    sa = Session(bind=db_engine)
    sb = Session(bind=db_engine)
    try:
        sb.execute(text(_LOCK_TIMEOUT))
        loaded = sb.get(Guardian, gid)  # 持有參照：identity map 是弱參照，沒人持有就會重新載入
        assert loaded is not None
        assert loaded.archived_at is None
        sa.execute(text(_LOCK_TIMEOUT))
        archive_guardian(sa, gid, actor=_actor(), meta=_META, clock=clock)
        thread, done, outcome = _call_in_thread(
            sb, lambda s: update_guardian(s, gid, GuardianUpdateIn(is_primary=True), clock=clock)
        )
        try:
            assert not done.wait(timeout=0.5), outcome  # A 未 commit：B 在學生列鎖排隊
        finally:
            sa.commit()
        thread.join(timeout=15)
        assert done.is_set(), "A commit 後 thread 仍未完成"
        assert outcome.get("result") == (404, "guardian_not_found"), outcome
        assert loaded.archived_at == _NOW  # populate_existing 把上鎖後的值寫回同一物件
    finally:
        sa.close()
        sb.close()

    with Session(bind=db_engine) as check:
        stored = check.get(Guardian, gid)
        assert stored is not None
        assert stored.archived_at == _NOW
        assert stored.is_primary is False


@pytest.mark.cleanup_tables("class_staff")
def test_guardian_concurrent_unbind_only_one_succeeds(
    owner_cleanup_guardians: dict[str, list[UUID]],
    committing_db_session: Session,
    db_engine: Engine,
) -> None:
    """已綁定的 G，兩位員工同時解除綁定：B（事先載入並持有 G，顯示已綁定）在學生列鎖排隊，A commit
    後 B 重讀到 parent_account_id 已清空 → 409 guardian_not_bound；guardian.unbind 稽核恰 1 筆。
    （沒有鎖 / 沒有 populate_existing 時兩邊都成功、稽核兩筆。）"""
    db = committing_db_session
    student = make_student(db)
    parent = make_parent(db)
    guardian = make_guardian(db, student, parent=parent, name="王媽媽")
    db.commit()
    owner_cleanup_guardians["guardians"].append(guardian.id)
    owner_cleanup_guardians["students"].append(student.id)
    owner_cleanup_guardians["parents"].append(parent.id)
    gid, pid = guardian.id, parent.id
    clock = _clock()

    sa = Session(bind=db_engine)
    sb = Session(bind=db_engine)
    try:
        sb.execute(text(_LOCK_TIMEOUT))
        loaded = sb.get(Guardian, gid)
        assert loaded is not None
        assert loaded.parent_account_id == pid
        sa.execute(text(_LOCK_TIMEOUT))
        out_a = unbind_guardian(sa, gid, actor=_actor(), meta=_META, clock=clock)
        thread, done, outcome = _call_in_thread(
            sb, lambda s: unbind_guardian(s, gid, actor=_actor(), meta=_META, clock=clock)
        )
        try:
            assert not done.wait(timeout=0.5), outcome  # A 未 commit：B 在學生列鎖排隊
        finally:
            sa.commit()
        thread.join(timeout=15)
        assert done.is_set(), "A commit 後 thread 仍未完成"
        assert outcome.get("result") == (409, "guardian_not_bound"), outcome
        assert loaded.parent_account_id is None  # populate_existing 寫回同一物件
    finally:
        sa.close()
        sb.close()

    assert out_a.binding.status == "unbound"
    with Session(bind=db_engine) as check:
        stored = check.get(Guardian, gid)
        assert stored is not None
        assert stored.parent_account_id is None
        assert stored.archived_at is None
        audits = _unbind_audits(check, gid)
        assert len(audits) == 1
        assert audits[0].before == {"parent_account_id": str(pid)}
        assert get_parent_student_ids(check, pid) == []


@pytest.mark.usefixtures("secret_env")
@pytest.mark.cleanup_tables("class_staff")
def test_guardian_concurrent_archive_waits_for_in_flight_claim(
    owner_cleanup_guardians: dict[str, list[UUID]],
    committing_db_session: Session,
    db_engine: Engine,
) -> None:
    """家長端 claim（binding_code_service：不鎖學生列，只對 G 列條件式 UPDATE）正把 G 綁到家長 P、
    尚未 commit；員工同時 archive_guardian(G)：學生列鎖拿得到，必須在 G 列的 FOR UPDATE 等 claim
    commit，重讀後看到 parent_account_id 才會寫 guardian.unbind（reason=archived）稽核並把家長的
    可見範圍切斷。（沒有 FOR UPDATE 時 B 以普通 SELECT 讀到「未綁定」、不寫稽核，之後的 UPDATE 才
    等鎖——家長的可見範圍被切斷卻沒有稽核。）"""
    db = committing_db_session
    staff = make_staff(db, role_code="tutor")
    student = make_student(db)
    parent = make_parent(db)
    guardian = make_guardian(db, student, name="王媽媽")
    issued = generate(
        db, guardian_id=guardian.id, actor=_staff_actor(staff), meta=_META, clock=_clock()
    )
    db.commit()
    owner_cleanup_guardians["guardians"].append(guardian.id)
    owner_cleanup_guardians["students"].append(student.id)
    owner_cleanup_guardians["parents"].append(parent.id)
    owner_cleanup_guardians["staff"].append(staff.id)
    gid, pid = guardian.id, parent.id
    clock = _clock()

    sc = Session(bind=db_engine)
    sb = Session(bind=db_engine)
    try:
        sc.execute(text(_LOCK_TIMEOUT))
        bound = claim(sc, raw_code=issued.code, parent_account_id=pid, clock=clock)
        assert bound.parent_account_id == pid
        sb.execute(text(_LOCK_TIMEOUT))
        thread, done, outcome = _call_in_thread(
            sb, lambda s: archive_guardian(s, gid, actor=_actor(), meta=_META, clock=clock)
        )
        try:
            assert not done.wait(timeout=0.5), outcome  # claim 未 commit：archive 在 G 列鎖排隊
        finally:
            sc.commit()
        thread.join(timeout=15)
        assert done.is_set(), "claim commit 後 thread 仍未完成"
    finally:
        sc.close()
        sb.close()

    assert "error" not in outcome, outcome
    assert outcome.get("result") is None  # archive_guardian 成功（無回傳值）
    with Session(bind=db_engine) as check:
        stored = check.get(Guardian, gid)
        assert stored is not None
        assert stored.archived_at == _NOW
        assert stored.parent_account_id == pid  # 歷史保留
        assert stored.is_primary is False
        audits = _unbind_audits(check, gid)
        assert len(audits) == 1
        assert audits[0].before == {"parent_account_id": str(pid)}
        assert audits[0].after == {"reason": "archived"}
        assert get_parent_student_ids(check, pid) == []
