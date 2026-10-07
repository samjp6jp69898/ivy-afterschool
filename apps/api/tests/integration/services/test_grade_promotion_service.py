"""BACKEND-157：app/services/grade_promotion_service.py（preview，學年升級預覽）。

對象：未封存且 status ∈ {active, suspended}；1~5 年級 promote（grade +1）、6 年級 graduate；
依 grade、student_no 排序；class_id 不變（class_name 顯示原班）；已執行過該學年度升級 →
already_promoted。

BACKEND-158：execute（學年升級執行）。advisory lock 互斥、已執行 409 already_promoted、
expected_total 不符 409 preview_stale、六年級先轉 withdrawn 再把 1~5 年級 +1、畢業生同交易
close_out、稽核 student.promote_grade；入班日晚於退班日的六年級 → 422 invalid_dates 整批不動。
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from datetime import date, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session

from app.api.deps import CurrentStaff
from app.core.config import get_settings
from app.core.crypto import derive_key
from app.core.errors import AppError
from app.core.request_meta import RequestMeta
from app.models.audit import AuditLog
from app.models.students import Student
from app.schemas.students import StudentUpdateIn
from app.services import audit_service, grade_promotion_service
from app.services.grade_promotion_service import (
    PromotionItem,
    PromotionPreview,
    PromotionResult,
    execute,
    is_already_promoted,
    preview,
)
from app.services.student_service import update_student
from tests.integration.db.conftest import connect_owner
from tests.support.factories import (
    make_class,
    make_pickup_authorization,
    make_pickup_request,
    make_student,
)
from tests.support.fake_clock import FakeClock

_META = RequestMeta(ip="127.0.0.1", user_agent="pytest", request_id="req-1")


@pytest.fixture(autouse=True)
def _crypto_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    # make_pickup_authorization 以 HMAC 算接送碼，需要 APP_SECRET_KEY
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


def test_promotion_preview_classify(db_session: Session) -> None:
    klass = make_class(db_session, name="彩虹班")
    first = make_student(db_session, name="王小明", grade_level=1, class_=klass)
    fifth = make_student(db_session, name="陳小華", grade_level=5, status="suspended")
    sixth = make_student(db_session, name="林小安", grade_level=6)
    # DB CHECK：withdrawn 必須有 withdrawn_on，先建 active 再轉
    withdrawn = make_student(db_session, name="張小芳", grade_level=3)
    withdrawn.status = "withdrawn"
    withdrawn.withdrawn_on = date(2026, 7, 31)
    make_student(db_session, name="李小兵", grade_level=2, archived=True)
    db_session.flush()

    result = preview(db_session, from_academic_year=115)

    assert isinstance(result, PromotionPreview)
    assert result.from_academic_year == 115
    assert result.to_academic_year == 116
    assert [(i.grade_from, i.grade_to) for i in result.promote] == [(1, 2), (5, 6)]
    assert [i.id for i in result.promote] == [first.id, fifth.id]
    assert result.promote[0] == PromotionItem(
        id=first.id,
        student_no=first.student_no,
        name="王小明",
        grade_from=1,
        grade_to=2,
        class_name="彩虹班",
    )
    assert result.promote[1].class_name is None
    assert [i.id for i in result.graduate] == [sixth.id]
    assert result.graduate[0].grade_from == 6
    assert result.graduate[0].grade_to is None
    assert result.total == 3
    assert result.total == len(result.promote) + len(result.graduate)
    assert result.already_promoted is False


def test_promotion_preview_sorted_by_grade_then_student_no(db_session: Session) -> None:
    b = make_student(db_session, name="乙", grade_level=2, student_no="S-PRM-002")
    a = make_student(db_session, name="甲", grade_level=2, student_no="S-PRM-001")
    c = make_student(db_session, name="丙", grade_level=1, student_no="S-PRM-003")
    g2 = make_student(db_session, name="戊", grade_level=6, student_no="S-PRM-006")
    g1 = make_student(db_session, name="丁", grade_level=6, student_no="S-PRM-005")

    result = preview(db_session, from_academic_year=115)

    # 其他測試資料已 rollback，DB 內只有本測試的學生
    assert [i.id for i in result.promote] == [c.id, a.id, b.id]
    assert [i.id for i in result.graduate] == [g1.id, g2.id]
    assert result.total == 5


def test_promotion_preview_already_flag(db_session: Session) -> None:
    make_student(db_session, name="王小明", grade_level=1)
    audit_service.record(
        db_session,
        actor=audit_service.Actor.system(),
        action="student.promote_grade",
        entity_type="academic_year",
        entity_id="114",
        after={"promoted": 1, "graduated": 0, "to_academic_year": 115},
    )

    assert is_already_promoted(db_session, 114) is True
    assert preview(db_session, from_academic_year=114).already_promoted is True
    assert preview(db_session, from_academic_year=115).already_promoted is False
    # 仍回結果讓前端提示
    assert preview(db_session, from_academic_year=114).total == 1


# --- BACKEND-158：execute -------------------------------------------------------------------------


def _actor(*permissions: str) -> CurrentStaff:
    return CurrentStaff(
        id=uuid4(),
        username="clerk",
        display_name="陳行政",
        role_id=uuid4(),
        role_code="clerk",
        role_name="行政",
        permissions=frozenset(permissions),
        must_change_password=False,
        token_version=0,
    )


def _execute(
    db: Session,
    clock: FakeClock,
    *,
    from_academic_year: int = 114,
    expected_total: int,
    withdrawn_on: date | None = None,
    actor: CurrentStaff | None = None,
) -> PromotionResult:
    return execute(
        db,
        from_academic_year=from_academic_year,
        expected_total=expected_total,
        withdrawn_on=withdrawn_on,
        actor=actor or _actor("students:write"),
        meta=_META,
        clock=clock,
    )


def _state(db: Session, *students: Student) -> list[tuple[int, str, date | None]]:
    db.expire_all()
    return [(s.grade_level, s.status, s.withdrawn_on) for s in students]


def _promote_audits(db: Session, academic_year: int) -> list[AuditLog]:
    return list(
        db.execute(
            select(AuditLog).where(
                AuditLog.action == "student.promote_grade",
                AuditLog.entity_type == "academic_year",
                AuditLog.entity_id == str(academic_year),
            )
        ).scalars()
    )


def _close_out_audits(db: Session, student_id: UUID) -> int:
    return int(
        db.execute(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.action == "student.close_out", AuditLog.entity_id == str(student_id))
        ).scalar_one()
    )


def test_promotion_execute_success(db_session: Session, fake_clock: FakeClock) -> None:
    klass = make_class(db_session, name="彩虹班")
    first = make_student(db_session, name="王小明", grade_level=1, class_=klass)
    fifth = make_student(db_session, name="陳小華", grade_level=5, class_=klass)
    sixth = make_student(db_session, name="林小安", grade_level=6, class_=klass)
    sixth.enrolled_on = date(2020, 9, 1)
    db_session.flush()

    result = _execute(db_session, fake_clock, expected_total=3)

    assert isinstance(result, PromotionResult)
    assert result == PromotionResult(promoted=2, graduated=1)
    assert _state(db_session, first, fifth, sixth) == [
        (2, "active", None),
        (6, "active", None),  # 五年級變六年級，不會被同一次執行轉為退班
        (6, "withdrawn", fake_clock.today()),
    ]
    # 保留原安親班班級（之後由行政手動調班）
    assert (first.class_id, fifth.class_id, sixth.class_id) == (klass.id, klass.id, klass.id)
    assert preview(db_session, from_academic_year=114).already_promoted is True


def test_promotion_execute_scope_and_explicit_date(
    db_session: Session, fake_clock: FakeClock
) -> None:
    """suspended 也升級 / 畢業；withdrawn 與封存學生不動；給 withdrawn_on 時以給的為準。"""
    suspended_fifth = make_student(db_session, name="甲", grade_level=5, status="suspended")
    suspended_sixth = make_student(db_session, name="乙", grade_level=6, status="suspended")
    withdrawn = make_student(db_session, name="丙", grade_level=3)
    withdrawn.status = "withdrawn"
    withdrawn.withdrawn_on = date(2026, 7, 31)
    archived = make_student(db_session, name="丁", grade_level=2, archived=True)
    db_session.flush()

    result = _execute(db_session, fake_clock, expected_total=2, withdrawn_on=date(2026, 7, 31))

    assert result == PromotionResult(promoted=1, graduated=1)
    assert _state(db_session, suspended_fifth, suspended_sixth, withdrawn, archived) == [
        (6, "suspended", None),
        (6, "withdrawn", date(2026, 7, 31)),
        (3, "withdrawn", date(2026, 7, 31)),
        (2, "active", None),
    ]


def test_promotion_execute_already(db_session: Session, fake_clock: FakeClock) -> None:
    first = make_student(db_session, name="王小明", grade_level=1)
    _execute(db_session, fake_clock, expected_total=1)

    with pytest.raises(AppError) as exc:
        _execute(db_session, fake_clock, expected_total=1)

    assert (exc.value.status, exc.value.code) == (409, "already_promoted")
    assert _state(db_session, first) == [(2, "active", None)]  # 沒有被升第二次
    assert len(_promote_audits(db_session, 114)) == 1
    # 別的學年度不受影響
    assert _execute(db_session, fake_clock, from_academic_year=115, expected_total=1) == (
        PromotionResult(promoted=1, graduated=0)
    )


def test_promotion_execute_stale(db_session: Session, fake_clock: FakeClock) -> None:
    first = make_student(db_session, name="王小明", grade_level=1)
    sixth = make_student(db_session, name="林小安", grade_level=6)
    db_session.flush()

    with pytest.raises(AppError) as exc:
        _execute(db_session, fake_clock, expected_total=5)

    assert (exc.value.status, exc.value.code) == (409, "preview_stale")
    assert exc.value.details == {"expected_total": 5, "total": 2}
    assert _state(db_session, first, sixth) == [(1, "active", None), (6, "active", None)]
    assert _promote_audits(db_session, 114) == []
    assert is_already_promoted(db_session, 114) is False


def test_promotion_execute_audit(db_session: Session, fake_clock: FakeClock) -> None:
    make_student(db_session, name="王小明", grade_level=1)
    make_student(db_session, name="陳小華", grade_level=4)
    make_student(db_session, name="林小安", grade_level=6)
    actor = _actor("students:write")

    _execute(db_session, fake_clock, expected_total=3, actor=actor)

    audits = _promote_audits(db_session, 114)
    assert len(audits) == 1
    audit = audits[0]
    assert (audit.entity_type, audit.entity_id) == ("academic_year", "114")
    assert audit.after == {"promoted": 2, "graduated": 1, "to_academic_year": 115}
    assert audit.before is None
    assert (audit.actor_type, audit.actor_id) == ("staff", actor.id)
    assert (audit.ip, audit.user_agent) == ("127.0.0.1", "pytest")


@pytest.mark.clock("2026-08-15T10:00:00+08:00")
def test_promotion_execute_closes_out_graduates(db_session: Session, fake_clock: FakeClock) -> None:
    """畢業生逐一 close_out（BACKEND-529）：今天起的接送請求 / 代理授權取消；升級生不受影響。"""
    today = fake_clock.today()
    sixth = make_student(db_session, name="林小安", grade_level=6)
    fifth = make_student(db_session, name="陳小華", grade_level=5)
    graduate_request = make_pickup_request(db_session, sixth, service_date=today)
    graduate_auth = make_pickup_authorization(
        db_session, sixth, service_date=today + timedelta(days=1), code="123456"
    )
    fifth_request = make_pickup_request(db_session, fifth, service_date=today)

    result = _execute(db_session, fake_clock, expected_total=2)

    assert result == PromotionResult(promoted=1, graduated=1)
    db_session.expire_all()
    assert (graduate_request.status, graduate_request.cancel_reason) == ("cancelled", "學生已退班")
    assert graduate_auth.status == "cancelled"
    assert fifth_request.status == "pending"
    assert _close_out_audits(db_session, sixth.id) == 1
    assert _close_out_audits(db_session, fifth.id) == 0
    assert (sixth.status, sixth.withdrawn_on) == ("withdrawn", today)


@pytest.mark.clock("2026-08-15T10:00:00+08:00")
def test_promotion_execute_invalid_dates(db_session: Session, fake_clock: FakeClock) -> None:
    """六年級入班日晚於退班日會撞 DB CHECK：以明確 422 擋下並指出學生，整批不動。"""
    first = make_student(db_session, name="王小明", grade_level=1)
    late = make_student(db_session, name="林小安", grade_level=6, student_no="S-PRM-LATE")
    late.enrolled_on = date(2026, 9, 1)
    ok_sixth = make_student(db_session, name="黃小美", grade_level=6)
    ok_sixth.enrolled_on = date(2026, 8, 15)  # 等於退班日可以
    db_session.flush()

    with pytest.raises(AppError) as exc:
        _execute(db_session, fake_clock, expected_total=3)

    assert (exc.value.status, exc.value.code) == (422, "invalid_dates")
    assert exc.value.details == {
        "withdrawn_on": date(2026, 8, 15),
        "students": [
            {
                "id": late.id,
                "student_no": "S-PRM-LATE",
                "name": "林小安",
                "enrolled_on": date(2026, 9, 1),
            }
        ],
    }
    assert _state(db_session, first, late, ok_sixth) == [
        (1, "active", None),
        (6, "active", None),
        (6, "active", None),
    ]
    assert _promote_audits(db_session, 114) == []
    # 指定不早於入班日的退班日就能執行
    result = _execute(db_session, fake_clock, expected_total=3, withdrawn_on=date(2026, 9, 1))
    assert result == PromotionResult(promoted=1, graduated=2)
    assert _state(db_session, late, ok_sixth) == [
        (6, "withdrawn", date(2026, 9, 1)),
        (6, "withdrawn", date(2026, 9, 1)),
    ]


@pytest.fixture
def owner_cleanup_rows() -> Iterator[list[tuple[str, str, object]]]:
    """committing 測試建立的列以 owner 連線依登記反序刪除 ``(table, column, value)``。"""
    rows: list[tuple[str, str, object]] = []
    yield rows
    with connect_owner() as conn:
        conn.execute("set lock_timeout = '5s'")
        for table, column, value in reversed(rows):
            conn.execute(
                f"delete from public.{table} where {column} = %s",  # noqa: S608  表名欄名為測試常數
                (value,),
            )
        conn.commit()


_CONCURRENT_YEAR = 150  # 只有本測試用的學年度，owner 清理 audit_logs 不會誤刪別人的列


@pytest.mark.cleanup_tables("class_staff")
def test_promotion_execute_concurrent_second_run_waits_then_409(
    owner_cleanup_rows: list[tuple[str, str, object]],
    committing_db_session: Session,
    db_engine: Engine,
    fake_clock: FakeClock,
) -> None:
    """同時按兩次：A 持 advisory lock 未 commit → B 必須等待（0.5 秒內未結束）→ A commit →
    B 看到稽核 → 409 already_promoted，學生只被升一次。沒有 advisory lock 時 B 會在 A commit 後
    重新評估、再升一次（一年級變三年級、剛升上的六年級被轉退班）。"""
    first = make_student(committing_db_session, name="王小明", grade_level=1)
    fifth = make_student(committing_db_session, name="陳小華", grade_level=5)
    committing_db_session.commit()
    first_id, fifth_id = first.id, fifth.id
    # 實作有誤（沒鎖、順序錯）時五年級會被當畢業生 close_out 並 commit：連 close_out 稽核一起清
    owner_cleanup_rows.extend(
        [
            ("students", "id", first_id),
            ("students", "id", fifth_id),
            ("audit_logs", "entity_id", str(_CONCURRENT_YEAR)),
            ("audit_logs", "entity_id", str(first_id)),
            ("audit_logs", "entity_id", str(fifth_id)),
        ]
    )
    a_locked = threading.Event()
    release_a = threading.Event()
    b_done = threading.Event()
    outcome: dict[str, object] = {}

    def worker_a() -> None:
        sa = Session(bind=db_engine)
        try:
            sa.execute(text("set local lock_timeout = '15s'"))
            outcome["a"] = _execute(
                sa, fake_clock, from_academic_year=_CONCURRENT_YEAR, expected_total=2
            )
            a_locked.set()
            release_a.wait(timeout=15)
            sa.commit()
        except BaseException as exc:
            sa.rollback()
            outcome["a"] = exc
            a_locked.set()
        finally:
            sa.close()

    def worker_b() -> None:
        sb = Session(bind=db_engine)
        try:
            a_locked.wait(timeout=15)
            sb.execute(text("set local lock_timeout = '15s'"))
            try:
                outcome["b"] = _execute(
                    sb, fake_clock, from_academic_year=_CONCURRENT_YEAR, expected_total=2
                )
                sb.commit()
            except AppError as exc:
                sb.rollback()
                outcome["b"] = (exc.status, exc.code)
        except BaseException as exc:
            sb.rollback()
            outcome["b_error"] = exc
        finally:
            sb.close()
            b_done.set()

    threads = [threading.Thread(target=worker_a), threading.Thread(target=worker_b)]
    for t in threads:
        t.start()
    try:
        assert a_locked.wait(timeout=15)
        assert not b_done.wait(timeout=0.5), outcome  # A 尚未 commit：B 在 advisory lock 等待
    finally:
        release_a.set()
        for t in threads:
            t.join(timeout=30)

    assert outcome.get("a") == PromotionResult(promoted=2, graduated=0), outcome
    assert "b_error" not in outcome, outcome
    assert outcome["b"] == (409, "already_promoted")
    with Session(bind=db_engine) as check:
        rows = check.execute(
            select(Student.id, Student.grade_level, Student.status).where(
                Student.id.in_([first_id, fifth_id])
            )
        ).all()
        assert {(r[0], r[1], r[2]) for r in rows} == {
            (first_id, 2, "active"),
            (fifth_id, 6, "active"),
        }
        assert len(_promote_audits(check, _CONCURRENT_YEAR)) == 1


# --- BACKEND-553：execute 對象列 FOR UPDATE 的並發回歸 -------------------------------------------
# 兩條 app_backend 連線 + threading 阻塞模式；lock_timeout 一律 SET LOCAL（連線會回 pool）。
# 每個測試各用一個學年度：owner 清理 audit_logs 不會誤刪別人的列，也不會互相撞 already_promoted。

_LOCK_TIMEOUT = "set local lock_timeout = '15s'"
_ROW_LOCK_YEAR = 151
_WAITS_YEAR = 152
_STALE_YEAR = 153


def _call_in_thread(
    sb: Session, call: Callable[[Session], object]
) -> tuple[threading.Thread, threading.Event, dict[str, object]]:
    """在 thread 以 ``sb`` 執行 ``call``：成功 → commit 並記回傳值；AppError → 記 (status, code) 與
    details，交易留給呼叫端關閉；其他例外原樣記在 ``error``。"""
    done = threading.Event()
    outcome: dict[str, object] = {}

    def worker() -> None:
        try:
            try:
                outcome["result"] = call(sb)
                sb.commit()
            except AppError as exc:
                outcome["result"] = (exc.status, exc.code)
                outcome["details"] = exc.details
        except BaseException as exc:
            outcome["error"] = exc
        finally:
            done.set()

    thread = threading.Thread(target=worker)
    thread.start()
    return thread, done, outcome


def _committed_targets(
    db: Session, cleanup: list[tuple[str, str, object]], year: int, grades: tuple[int, ...]
) -> list[UUID]:
    """對象學生（1~5 年級，沒有畢業生）commit 後登記 owner 清理：學生列、該學年度的升級稽核、
    各學生的 student.update / student.close_out 稽核。"""
    students = [
        make_student(db, name=f"王小明{index}", grade_level=grade)
        for index, grade in enumerate(grades, start=1)
    ]
    db.commit()
    ids = [student.id for student in students]
    cleanup.extend(("students", "id", sid) for sid in ids)
    cleanup.append(("audit_logs", "entity_id", str(year)))
    cleanup.extend(("audit_logs", "entity_id", str(sid)) for sid in ids)
    return ids


def _withdraw(db: Session, student_id: UUID, clock: FakeClock) -> None:
    update_student(
        db,
        student_id,
        StudentUpdateIn(status="withdrawn"),
        actor=_actor("students:write"),
        meta=_META,
        clock=clock,
    )


def _grades(db: Session, ids: list[UUID]) -> dict[UUID, tuple[int, str, date | None]]:
    rows = db.execute(
        select(Student.id, Student.grade_level, Student.status, Student.withdrawn_on).where(
            Student.id.in_(ids)
        )
    )
    return {sid: (grade, status, withdrawn_on) for sid, grade, status, withdrawn_on in rows}


@pytest.mark.cleanup_tables("class_staff")
def test_promotion_execute_lock_blocks_on_target_student_row(
    owner_cleanup_rows: list[tuple[str, str, object]],
    committing_db_session: Session,
    db_engine: Engine,
    fake_clock: FakeClock,
) -> None:
    """外部交易對一位對象學生列持 FOR KEY SHARE（等同另一交易正在插入參照該生的出勤 / 接送列、
    尚未 commit）：execute 鎖對象列的 FOR UPDATE 必須排隊——放鎖前 0.5 秒內未完成，放鎖後完成
    且兩位都升級。（沒有對象列 FOR UPDATE 時 bulk update 只改非鍵欄位 grade_level，與 KEY SHARE
    不衝突、立刻完成；外部若改持 FOR UPDATE，沒先鎖列的實作也會在 UPDATE 等鎖，分不出差別。）"""
    ming_id, hua_id = _committed_targets(
        committing_db_session, owner_cleanup_rows, _ROW_LOCK_YEAR, (3, 1)
    )
    total = preview(committing_db_session, from_academic_year=_ROW_LOCK_YEAR).total
    committing_db_session.rollback()

    holder = Session(bind=db_engine)
    sb = Session(bind=db_engine)
    try:
        holder.execute(text(_LOCK_TIMEOUT))
        holder.execute(
            text("select id from students where id = :id for key share"), {"id": ming_id}
        )
        sb.execute(text(_LOCK_TIMEOUT))
        thread, done, outcome = _call_in_thread(
            sb,
            lambda s: _execute(
                s, fake_clock, from_academic_year=_ROW_LOCK_YEAR, expected_total=total
            ),
        )
        try:
            assert not done.wait(timeout=0.5), outcome  # 對象列被鎖住：execute 必須排隊
        finally:
            holder.rollback()  # 放鎖
        thread.join(timeout=15)
        assert done.is_set(), "放鎖後 thread 仍未完成"
    finally:
        holder.close()
        sb.close()

    assert "error" not in outcome, outcome
    assert outcome["result"] == PromotionResult(promoted=total, graduated=0)
    with Session(bind=db_engine) as check:
        assert _grades(check, [ming_id, hua_id]) == {
            ming_id: (4, "active", None),
            hua_id: (2, "active", None),
        }
        assert len(_promote_audits(check, _ROW_LOCK_YEAR)) == 1


@pytest.mark.cleanup_tables("class_staff")
def test_promotion_execute_lock_update_student_waits_and_sees_promoted_grade(
    owner_cleanup_rows: list[tuple[str, str, object]],
    committing_db_session: Session,
    db_engine: Engine,
    fake_clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 的 execute 鎖住對象列、重算 preview 後暫停（以 monkeypatch 包住 preview 注入停頓；
    尚未 bulk update、未 commit）；B 對同一位對象學生 update_student(status=withdrawn) 在
    學生列鎖排隊（0.5 秒內未完成）→ A 放行並 commit → B 以 FOR UPDATE 重讀到升級後的年級再退班：
    最終 grade_level 為升級後的值且 status=withdrawn，另一位照常升級。（沒有對象列 FOR UPDATE
    時 A 停頓期間不持任何列鎖，B 立刻完成。）"""
    ming_id, hua_id = _committed_targets(
        committing_db_session, owner_cleanup_rows, _WAITS_YEAR, (3, 1)
    )
    total = preview(committing_db_session, from_academic_year=_WAITS_YEAR).total
    committing_db_session.rollback()
    original_preview = grade_promotion_service.preview
    a_locked = threading.Event()
    release_a = threading.Event()
    b_done = threading.Event()
    outcome: dict[str, object] = {}

    def paused_preview(session: Session, *, from_academic_year: int) -> PromotionPreview:
        result = original_preview(session, from_academic_year=from_academic_year)
        a_locked.set()  # 對象列已鎖、名單已算：在寫入前停住
        release_a.wait(timeout=15)
        return result

    monkeypatch.setattr(grade_promotion_service, "preview", paused_preview)

    def worker_a() -> None:
        sa = Session(bind=db_engine)
        try:
            sa.execute(text(_LOCK_TIMEOUT))
            outcome["a"] = _execute(
                sa, fake_clock, from_academic_year=_WAITS_YEAR, expected_total=total
            )
            sa.commit()
        except BaseException as exc:
            sa.rollback()
            outcome["a"] = exc
            a_locked.set()
        finally:
            sa.close()

    def worker_b() -> None:
        sb = Session(bind=db_engine)
        try:
            a_locked.wait(timeout=15)
            sb.execute(text(_LOCK_TIMEOUT))
            _withdraw(sb, ming_id, fake_clock)
            sb.commit()
            outcome["b"] = "ok"
        except BaseException as exc:
            sb.rollback()
            outcome["b"] = exc
        finally:
            sb.close()
            b_done.set()

    threads = [threading.Thread(target=worker_a), threading.Thread(target=worker_b)]
    for t in threads:
        t.start()
    try:
        assert a_locked.wait(timeout=15)
        assert not b_done.wait(timeout=0.5), outcome  # A 持對象列鎖未 commit：B 必須排隊
    finally:
        release_a.set()
        for t in threads:
            t.join(timeout=30)

    assert outcome.get("a") == PromotionResult(promoted=total, graduated=0), outcome
    assert outcome.get("b") == "ok", outcome
    with Session(bind=db_engine) as check:
        assert _grades(check, [ming_id, hua_id]) == {
            ming_id: (4, "withdrawn", fake_clock.today()),  # 升級後才退班
            hua_id: (2, "active", None),
        }
        assert len(_promote_audits(check, _WAITS_YEAR)) == 1
        assert _close_out_audits(check, ming_id) == 1


@pytest.mark.cleanup_tables("class_staff")
def test_promotion_execute_lock_stale_preview_409(
    owner_cleanup_rows: list[tuple[str, str, object]],
    committing_db_session: Session,
    db_engine: Engine,
    fake_clock: FakeClock,
) -> None:
    """B 的 update_student(status=withdrawn) 持有一位對象學生的列鎖、尚未 commit；A 的 execute
    在鎖對象列時排隊（0.5 秒內未完成）→ B commit → A 的 FOR UPDATE 重新評估、該生已非對象，重算
    preview 的總數與 expected_total 不符 → 409 preview_stale，整批不動、沒有升級稽核。（沒有對象列
    FOR UPDATE 時 A 以舊名單通過檢查，bulk update 等到 B commit 後連已退班的學生一起升級。）"""
    ming_id, hua_id = _committed_targets(
        committing_db_session, owner_cleanup_rows, _STALE_YEAR, (3, 1)
    )
    total = preview(committing_db_session, from_academic_year=_STALE_YEAR).total
    committing_db_session.rollback()

    sb = Session(bind=db_engine)
    sa = Session(bind=db_engine)
    try:
        sb.execute(text(_LOCK_TIMEOUT))
        _withdraw(sb, ming_id, fake_clock)  # 持學生列鎖、未 commit
        sa.execute(text(_LOCK_TIMEOUT))
        thread, done, outcome = _call_in_thread(
            sa,
            lambda s: _execute(s, fake_clock, from_academic_year=_STALE_YEAR, expected_total=total),
        )
        try:
            assert not done.wait(timeout=0.5), outcome  # B 持對象列鎖未 commit：A 必須排隊
        finally:
            sb.commit()
        thread.join(timeout=15)
        assert done.is_set(), "B commit 後 thread 仍未完成"
    finally:
        sb.close()
        sa.close()

    assert "error" not in outcome, outcome
    assert outcome["result"] == (409, "preview_stale")
    assert outcome["details"] == {"expected_total": total, "total": total - 1}
    with Session(bind=db_engine) as check:
        assert _grades(check, [ming_id, hua_id]) == {
            ming_id: (3, "withdrawn", fake_clock.today()),  # 退班、沒被升級
            hua_id: (1, "active", None),  # 整批不動
        }
        assert _promote_audits(check, _STALE_YEAR) == []
