"""BACKEND-135：app/services/class_service.py（list_classes）。
BACKEND-138：update_class（部分更新、封存班 409、同年同名 409）。
BACKEND-139：archive_class（有在學學生 409、withdrawn 不阻擋、冪等）。
BACKEND-140：set_class_staff（整批取代以差異更新、無效員工 422、空清單）。"""

import threading
from collections.abc import Callable, Iterator
from datetime import date
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.api.deps import CurrentStaff
from app.core.errors import AppError
from app.models.classes import ClassStaff, SchoolClass
from app.schemas.classes import (
    ClassCreateIn,
    ClassListQuery,
    ClassOut,
    ClassStaffItemIn,
    ClassStaffPutIn,
    ClassUpdateIn,
)
from app.services.class_service import (
    archive_class,
    create_class,
    get_class,
    list_classes,
    set_class_staff,
    update_class,
)
from tests.integration.db.conftest import connect_owner
from tests.support.factories import make_class, make_class_staff, make_staff, make_student
from tests.support.fake_clock import FakeClock


def _actor(staff_id: UUID) -> CurrentStaff:
    return CurrentStaff(
        id=staff_id,
        username="teacher",
        display_name="林老師",
        role_id=uuid4(),
        role_code="tutor",
        role_name="導師",
        permissions=frozenset(),
        must_change_password=False,
        token_version=0,
    )


def _mine(rows: list[ClassOut], ids: set[UUID]) -> list[ClassOut]:
    """只看本測試建立的班，避免 DB 內其他資料干擾。"""
    return [r for r in rows if r.id in ids]


def test_list_classes_archived_filter(db_session: Session) -> None:
    a = make_class(db_session, name="A班")
    b = make_class(db_session, name="B班", archived=True)
    actor = _actor(make_staff(db_session).id)
    ids = {a.id, b.id}

    default = _mine(list_classes(db_session, ClassListQuery(), actor=actor), ids)
    with_archived = _mine(
        list_classes(db_session, ClassListQuery(include_archived=True), actor=actor), ids
    )

    assert {c.name for c in default} == {"A班"}
    assert {c.name for c in with_archived} == {"A班", "B班"}
    assert next(c for c in with_archived if c.name == "B班").archived_at is not None


def test_list_classes_academic_year_filter(db_session: Session) -> None:
    old = make_class(db_session, academic_year=114)
    new = make_class(db_session, academic_year=115)
    actor = _actor(make_staff(db_session).id)

    rows = list_classes(db_session, ClassListQuery(academic_year=114), actor=actor)

    assert old.id in {r.id for r in rows}
    assert new.id not in {r.id for r in rows}
    assert {r.academic_year for r in rows} == {114}


def test_list_classes_mine(db_session: Session) -> None:
    teacher = make_staff(db_session)
    a = make_class(db_session, name="A班")
    make_class(db_session, name="C班")
    make_class_staff(db_session, a, teacher, role="lead")
    other = make_class(db_session, name="D班")
    make_class_staff(db_session, other, make_staff(db_session), role="lead")

    rows = list_classes(db_session, ClassListQuery(mine=True), actor=_actor(teacher.id))

    assert [r.id for r in rows] == [a.id]
    everyone = list_classes(db_session, ClassListQuery(), actor=_actor(teacher.id))
    assert {a.id, other.id} <= {r.id for r in everyone}


def test_list_classes_counts_and_staff(db_session: Session) -> None:
    klass = make_class(db_session, name="A班")
    empty = make_class(db_session, name="空班")
    for _ in range(2):
        make_student(db_session, class_=klass)
    withdrawn = make_student(db_session, class_=klass)
    withdrawn.status = "withdrawn"
    withdrawn.withdrawn_on = date(2026, 9, 1)
    make_student(db_session, class_=klass, archived=True)
    lead = make_staff(db_session, display_name="林老師")
    inactive = make_staff(db_session, display_name="陳老師", is_active=False)
    make_class_staff(db_session, klass, lead, role="lead")
    make_class_staff(db_session, klass, inactive, role="assistant")
    db_session.flush()

    rows = {r.id: r for r in list_classes(db_session, ClassListQuery(), actor=_actor(lead.id))}

    assert rows[klass.id].student_count == 2
    assert [(s.staff_user_id, s.display_name, s.role) for s in rows[klass.id].staff] == [
        (lead.id, "林老師", "lead")
    ]
    assert rows[empty.id].student_count == 0
    assert rows[empty.id].staff == []


def test_list_classes_order(db_session: Session) -> None:
    old = make_class(db_session, name="B班", academic_year=114)
    second = make_class(db_session, name="B班", academic_year=115)
    first_by_name = make_class(db_session, name="A班", academic_year=115)
    actor = _actor(make_staff(db_session).id)
    ids = {old.id, second.id, first_by_name.id}

    rows = _mine(list_classes(db_session, ClassListQuery(), actor=actor), ids)

    assert [r.id for r in rows] == [first_by_name.id, second.id, old.id]


def test_list_classes_order_sort_order_within_year(db_session: Session) -> None:
    first_by_sort = make_class(db_session, name="B班", academic_year=115)
    second_by_sort = make_class(db_session, name="A班", academic_year=115)
    first_by_sort.sort_order = 0
    second_by_sort.sort_order = 1
    other_year_low_sort = make_class(db_session, name="A班", academic_year=114)
    other_year_low_sort.sort_order = 0
    db_session.flush()
    actor = _actor(make_staff(db_session).id)
    ids = {first_by_sort.id, second_by_sort.id, other_year_low_sort.id}

    rows = _mine(list_classes(db_session, ClassListQuery(), actor=actor), ids)

    # 同學年先看 sort_order（B班 sort_order=0 在 A班 sort_order=1 前），學年仍優先於 sort_order
    assert [r.id for r in rows] == [first_by_sort.id, second_by_sort.id, other_year_low_sort.id]


def test_get_class_success(db_session: Session) -> None:
    klass = make_class(db_session, name="A班", grade_levels=(1, 2), academic_year=115)
    teacher = make_staff(db_session, display_name="林老師")
    inactive = make_staff(db_session, display_name="陳老師", is_active=False)
    make_class_staff(db_session, klass, teacher, role="lead")
    make_class_staff(db_session, klass, inactive, role="assistant")
    make_student(db_session, class_=klass)
    make_student(db_session, class_=klass, archived=True)
    db_session.flush()

    out = get_class(db_session, klass.id)

    assert isinstance(out, ClassOut)
    assert (out.id, out.name, out.grade_levels, out.academic_year) == (klass.id, "A班", [1, 2], 115)
    assert out.staff[0].display_name == "林老師"
    assert [s.display_name for s in out.staff] == ["林老師"]
    assert out.student_count == 1
    assert out.archived_at is None


def test_get_class_archived_and_missing(db_session: Session) -> None:
    archived = make_class(db_session, archived=True)

    out = get_class(db_session, archived.id)
    assert out.archived_at is not None

    with pytest.raises(AppError) as exc:
        get_class(db_session, uuid4())
    assert (exc.value.status, exc.value.code) == (404, "class_not_found")


def _count_classes(db: Session, year: int) -> int:
    return db.execute(
        select(func.count()).select_from(SchoolClass).where(SchoolClass.academic_year == year)
    ).scalar_one()


def test_create_class_success(db_session: Session) -> None:
    out = create_class(
        db_session, ClassCreateIn(name="低年級 A 班", grade_levels=[2, 1], academic_year=115)
    )

    assert out.name == "低年級 A 班"
    assert out.grade_levels == [1, 2]
    assert out.academic_year == 115
    assert out.student_count == 0
    assert out.staff == []
    assert out.sort_order == 0
    assert out.archived_at is None
    stored = db_session.execute(select(SchoolClass).where(SchoolClass.id == out.id)).scalar_one()
    assert stored.name == "低年級 A 班"


def test_create_class_conflict(db_session: Session) -> None:
    create_class(db_session, ClassCreateIn(name="低年級 A 班", grade_levels=[1], academic_year=115))
    before = _count_classes(db_session, 115)

    with pytest.raises(AppError) as exc:
        create_class(
            db_session, ClassCreateIn(name="低年級 a 班 ", grade_levels=[1], academic_year=115)
        )

    assert (exc.value.status, exc.value.code) == (409, "class_name_taken")
    assert _count_classes(db_session, 115) == before
    other_year = create_class(
        db_session, ClassCreateIn(name="低年級 a 班", grade_levels=[1], academic_year=116)
    )
    assert other_year.academic_year == 116


def test_create_class_archived_name_reusable(db_session: Session) -> None:
    make_class(db_session, name="高年級班", academic_year=115, archived=True)

    out = create_class(
        db_session, ClassCreateIn(name="高年級班", grade_levels=[5, 6], academic_year=115)
    )

    assert out.name == "高年級班"
    assert out.archived_at is None


# --- BACKEND-138：update_class --------------------------------------------------------------------


def test_update_class_partial(db_session: Session) -> None:
    klass = make_class(db_session, name="A班", grade_levels=(1, 2), academic_year=115)
    make_student(db_session, class_=klass)

    out = update_class(db_session, klass.id, ClassUpdateIn(sort_order=3))

    assert isinstance(out, ClassOut)
    assert out.sort_order == 3
    assert (out.name, out.grade_levels, out.academic_year) == ("A班", [1, 2], 115)
    assert out.student_count == 1
    stored = db_session.execute(select(SchoolClass).where(SchoolClass.id == klass.id)).scalar_one()
    assert (stored.name, stored.sort_order) == ("A班", 3)

    renamed = update_class(db_session, klass.id, ClassUpdateIn(name="B班", grade_levels=[3]))
    assert (renamed.name, renamed.grade_levels, renamed.sort_order) == ("B班", [3], 3)


def test_update_class_archived(db_session: Session) -> None:
    archived = make_class(db_session, name="舊班", archived=True)

    with pytest.raises(AppError) as exc:
        update_class(db_session, archived.id, ClassUpdateIn(name="新名"))

    assert (exc.value.status, exc.value.code) == (409, "class_archived")
    db_session.refresh(archived)
    assert archived.name == "舊班"
    with pytest.raises(AppError) as missing:
        update_class(db_session, uuid4(), ClassUpdateIn(name="新名"))
    assert (missing.value.status, missing.value.code) == (404, "class_not_found")


def test_update_class_conflict(db_session: Session) -> None:
    make_class(db_session, name="低年級 A 班", academic_year=115)
    other = make_class(db_session, name="低年級 B 班", academic_year=115)
    other_year = make_class(db_session, name="低年級 C 班", academic_year=114)

    with pytest.raises(AppError) as exc:
        update_class(db_session, other.id, ClassUpdateIn(name="低年級 a 班"))

    assert (exc.value.status, exc.value.code) == (409, "class_name_taken")
    # savepoint 已 rollback：原名不變、session 仍可用
    db_session.refresh(other)
    assert other.name == "低年級 B 班"
    # 不同學年同名不衝突；改學年撞到同名則衝突
    assert update_class(db_session, other_year.id, ClassUpdateIn(name="低年級 A 班")).name == (
        "低年級 A 班"
    )
    with pytest.raises(AppError) as year_conflict:
        update_class(db_session, other_year.id, ClassUpdateIn(academic_year=115))
    assert year_conflict.value.code == "class_name_taken"


# --- BACKEND-139：archive_class -------------------------------------------------------------------


def test_archive_class_success(db_session: Session, fake_clock: FakeClock) -> None:
    klass = make_class(db_session, name="A班")
    withdrawn = make_student(db_session, class_=klass)
    withdrawn.status = "withdrawn"
    withdrawn.withdrawn_on = date(2026, 8, 31)
    make_student(db_session, class_=klass, archived=True)
    teacher = make_staff(db_session)
    make_class_staff(db_session, klass, teacher, role="lead")
    db_session.flush()

    out = archive_class(db_session, klass.id, clock=fake_clock)

    assert out.archived_at == fake_clock.now()
    db_session.refresh(klass)
    assert klass.archived_at == fake_clock.now()
    # class_staff 保留（歷史）
    links = db_session.execute(
        select(func.count()).select_from(ClassStaff).where(ClassStaff.class_id == klass.id)
    ).scalar_one()
    assert links == 1


def test_archive_class_has_students(db_session: Session, fake_clock: FakeClock) -> None:
    klass = make_class(db_session, name="A班")
    make_student(db_session, class_=klass)
    suspended = make_student(db_session, class_=klass)
    suspended.status = "suspended"
    withdrawn = make_student(db_session, class_=klass)
    withdrawn.status = "withdrawn"
    withdrawn.withdrawn_on = date(2026, 8, 31)
    make_student(db_session, class_=klass, archived=True)
    db_session.flush()

    with pytest.raises(AppError) as exc:
        archive_class(db_session, klass.id, clock=fake_clock)

    assert (exc.value.status, exc.value.code) == (409, "class_has_students")
    assert exc.value.details == {"student_count": 2}
    db_session.refresh(klass)
    assert klass.archived_at is None
    with pytest.raises(AppError) as missing:
        archive_class(db_session, uuid4(), clock=fake_clock)
    assert (missing.value.status, missing.value.code) == (404, "class_not_found")


def test_archive_class_idempotent(db_session: Session, fake_clock: FakeClock) -> None:
    klass = make_class(db_session, name="A班")
    first = archive_class(db_session, klass.id, clock=fake_clock)
    fake_clock.advance(hours=1)

    again = archive_class(db_session, klass.id, clock=fake_clock)

    assert again.archived_at == first.archived_at
    assert again.archived_at != fake_clock.now()


# --- BACKEND-140：set_class_staff -----------------------------------------------------------------


def _staff_rows(db: Session, class_id: UUID) -> dict[UUID, ClassStaff]:
    rows = db.execute(select(ClassStaff).where(ClassStaff.class_id == class_id)).scalars().all()
    return {row.staff_user_id: row for row in rows}


def test_set_class_staff_replace(db_session: Session) -> None:
    klass = make_class(db_session, name="A班")
    s1 = make_staff(db_session, display_name="林老師")
    s2 = make_staff(db_session, display_name="陳老師")
    s3 = make_staff(db_session, display_name="黃老師")
    make_class_staff(db_session, klass, s1, role="lead")
    s2_link_id = make_class_staff(db_session, klass, s2, role="assistant").id

    out = set_class_staff(
        db_session,
        klass.id,
        ClassStaffPutIn(
            items=[
                ClassStaffItemIn(staff_user_id=s2.id, role="lead"),
                ClassStaffItemIn(staff_user_id=s3.id, role="assistant"),
            ]
        ),
    )

    assert {(s.staff_user_id, s.role) for s in out.staff} == {(s2.id, "lead"), (s3.id, "assistant")}
    assert [s.role for s in out.staff] == ["lead", "assistant"]
    rows = _staff_rows(db_session, klass.id)
    assert set(rows) == {s2.id, s3.id}
    assert rows[s2.id].id == s2_link_id
    assert rows[s2.id].role == "lead"


def test_set_class_staff_invalid(db_session: Session) -> None:
    klass = make_class(db_session, name="A班")
    ok = make_staff(db_session)
    make_class_staff(db_session, klass, ok, role="lead")
    inactive = make_staff(db_session, is_active=False)
    missing = uuid4()

    with pytest.raises(AppError) as exc:
        set_class_staff(
            db_session,
            klass.id,
            ClassStaffPutIn(
                items=[
                    ClassStaffItemIn(staff_user_id=ok.id, role="lead"),
                    ClassStaffItemIn(staff_user_id=inactive.id, role="assistant"),
                    ClassStaffItemIn(staff_user_id=missing, role="assistant"),
                ]
            ),
        )

    assert (exc.value.status, exc.value.code) == (422, "invalid_staff")
    assert set(exc.value.details["invalid"]) == {inactive.id, missing}
    # 原指派不變
    assert set(_staff_rows(db_session, klass.id)) == {ok.id}


def test_set_class_staff_empty(db_session: Session) -> None:
    klass = make_class(db_session, name="A班")
    make_class_staff(db_session, klass, make_staff(db_session), role="lead")
    make_class_staff(db_session, klass, make_staff(db_session), role="assistant")

    out = set_class_staff(db_session, klass.id, ClassStaffPutIn(items=[]))

    assert out.staff == []
    assert _staff_rows(db_session, klass.id) == {}


def test_set_class_staff_archived(db_session: Session) -> None:
    archived = make_class(db_session, archived=True)
    teacher = make_staff(db_session)

    with pytest.raises(AppError) as exc:
        set_class_staff(
            db_session,
            archived.id,
            ClassStaffPutIn(items=[ClassStaffItemIn(staff_user_id=teacher.id, role="lead")]),
        )

    assert (exc.value.status, exc.value.code) == (409, "class_archived")
    with pytest.raises(AppError) as missing:
        set_class_staff(db_session, uuid4(), ClassStaffPutIn(items=[]))
    assert (missing.value.status, missing.value.code) == (404, "class_not_found")


# --- 並發（review-r8-b 打回 138 / 139 / 140）：寫入前必須鎖班級列 ------------------------------

_LOCK_NOT_AVAILABLE = "55P03"


@pytest.fixture
def owner_cleanup() -> Iterator[list[tuple[str, UUID]]]:
    """committing 測試建立的列以 owner 連線依登記的反序刪除；排在 committing_db_session 之前
    （先 close session、truncate class_staff，再刪學生 / 班級 / 員工 / 角色）。"""
    rows: list[tuple[str, UUID]] = []
    yield rows
    with connect_owner() as conn:
        conn.execute("set lock_timeout = '5s'")
        for table, row_id in reversed(rows):
            conn.execute(f"delete from public.{table} where id = %s", (row_id,))  # noqa: S608  表名為測試常數
        conn.commit()


def _committed_class(db: Session, cleanup: list[tuple[str, UUID]]) -> UUID:
    klass = make_class(db)
    db.commit()
    cleanup.append(("classes", klass.id))
    return klass.id


def _committed_staff(db: Session, cleanup: list[tuple[str, UUID]]) -> UUID:
    staff = make_staff(db)
    db.commit()
    cleanup.append(("roles", staff.role_id))
    cleanup.append(("staff_users", staff.id))
    return staff.id


def _short_lock_timeout(session: Session) -> None:
    # SET LOCAL：連線會回 pool，session 級設定會污染其他測試
    session.execute(text("set local lock_timeout = '200ms'"))


@pytest.mark.cleanup_tables("class_staff")
def test_update_class_blocked_by_concurrent_archive(
    owner_cleanup: list[tuple[str, UUID]],
    committing_db_session: Session,
    db_engine: Engine,
    fake_clock: FakeClock,
) -> None:
    """班 A：s2 先載入（archived_at None）→ s1 封存並 commit → s2 update_class 必須重讀上鎖後的值
    （populate_existing）→ 409 class_archived。班 B：s1 封存未 commit → s2 update_class 等鎖
    （lock_timeout 內 55P03）；s1 commit 後再試 → 409。"""
    class_a = _committed_class(committing_db_session, owner_cleanup)
    class_b = _committed_class(committing_db_session, owner_cleanup)
    s1 = Session(bind=db_engine)
    s2 = Session(bind=db_engine)
    try:
        # 持有 ORM 物件的參照（identity map 是弱參照，沒人持有會被回收、下次重新載入）
        loaded = s2.get(SchoolClass, class_a)
        assert loaded is not None
        assert loaded.archived_at is None
        archive_class(s1, class_a, clock=fake_clock)
        s1.commit()
        with pytest.raises(AppError) as stale:
            update_class(s2, class_a, ClassUpdateIn(name="封存後改名"))
        assert (stale.value.status, stale.value.code) == (409, "class_archived")
        assert loaded.archived_at is not None  # populate_existing 把上鎖後的值寫回同一物件
        s2.rollback()

        archive_class(s1, class_b, clock=fake_clock)
        _short_lock_timeout(s2)
        with pytest.raises(OperationalError) as blocked:
            update_class(s2, class_b, ClassUpdateIn(name="封存後改名"))
        assert getattr(blocked.value.orig, "sqlstate", None) == _LOCK_NOT_AVAILABLE
        s2.rollback()
        s1.commit()
        with pytest.raises(AppError) as exc:
            update_class(s2, class_b, ClassUpdateIn(name="封存後改名"))
        assert (exc.value.status, exc.value.code) == (409, "class_archived")
        s2.rollback()
        names = {get_class(s2, cid).name for cid in (class_a, class_b)}
        assert "封存後改名" not in names
    finally:
        s1.close()
        s2.close()


@pytest.mark.cleanup_tables("class_staff")
def test_archive_class_blocked_by_concurrent_student_insert(
    owner_cleanup: list[tuple[str, UUID]],
    committing_db_session: Session,
    db_engine: Engine,
    fake_clock: FakeClock,
) -> None:
    """s1 新增 active 學生到該班未 commit（FK 檢查對班級列持 KEY SHARE）→ s2 archive_class 的
    FOR UPDATE 等鎖 55P03；s1 commit 後 s2 再封存 → 409 class_has_students。"""
    class_id = _committed_class(committing_db_session, owner_cleanup)
    s1 = Session(bind=db_engine)
    s2 = Session(bind=db_engine)
    try:
        klass = s1.get(SchoolClass, class_id)
        assert klass is not None
        student_id = make_student(s1, class_=klass).id
        owner_cleanup.append(("students", student_id))
        _short_lock_timeout(s2)
        with pytest.raises(OperationalError) as blocked:
            archive_class(s2, class_id, clock=fake_clock)
        assert getattr(blocked.value.orig, "sqlstate", None) == _LOCK_NOT_AVAILABLE
        s2.rollback()

        s1.commit()
        with pytest.raises(AppError) as exc:
            archive_class(s2, class_id, clock=fake_clock)
        assert (exc.value.status, exc.value.code) == (409, "class_has_students")
        assert exc.value.details == {"student_count": 1}
        s2.rollback()
        assert get_class(s2, class_id).archived_at is None
    finally:
        s1.close()
        s2.close()


def _run_blocked_pair(
    hold: Callable[[Session], None],
    blocked: Callable[[Session], None],
    db_engine: Engine,
) -> list[BaseException]:
    """A 在 s1 執行 ``hold``（持鎖未 commit）→ B 在 s2 執行 ``blocked``（必須被擋住）→ 確認 B 在
    0.5 秒內沒結束 → A commit → B 完成並 commit。回傳兩邊的例外清單（空 = 都成功）。"""
    a_locked = threading.Event()
    release_a = threading.Event()
    b_done = threading.Event()
    errors: list[BaseException] = []

    def worker_a() -> None:
        sa = Session(bind=db_engine)
        try:
            hold(sa)
            a_locked.set()
            release_a.wait(timeout=10)
            sa.commit()
        except BaseException as exc:
            errors.append(exc)
            a_locked.set()
        finally:
            sa.close()

    def worker_b() -> None:
        sb = Session(bind=db_engine)
        try:
            a_locked.wait(timeout=10)
            blocked(sb)
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
        assert a_locked.wait(timeout=10)
        assert not b_done.wait(timeout=0.5), errors  # A 尚未 commit：B 必須被擋住
    finally:
        release_a.set()
        for t in threads:
            t.join(timeout=10)
    assert b_done.is_set()
    return errors


@pytest.mark.cleanup_tables("class_staff")
def test_set_class_staff_serialized(
    owner_cleanup: list[tuple[str, UUID]],
    committing_db_session: Session,
    db_engine: Engine,
    fake_clock: FakeClock,
) -> None:
    """兩個 PUT 同時指派同一位員工：B 等 A 的班級列鎖；A commit 後 B 以「整批取代」覆蓋成自己的
    清單（一列、role 為 B 給的），不會撞 uq_class_staff_class_staff 落成通用 409 conflict。
    另外 s1 封存未 commit 時 s2 set_class_staff 要等鎖（55P03），commit 後 409 class_archived。"""
    class_id = _committed_class(committing_db_session, owner_cleanup)
    staff_id = _committed_staff(committing_db_session, owner_cleanup)
    results: dict[str, list[tuple[UUID, str]]] = {}

    def hold(sa: Session) -> None:
        set_class_staff(
            sa,
            class_id,
            ClassStaffPutIn(items=[ClassStaffItemIn(staff_user_id=staff_id, role="lead")]),
        )

    def blocked(sb: Session) -> None:
        out = set_class_staff(
            sb,
            class_id,
            ClassStaffPutIn(items=[ClassStaffItemIn(staff_user_id=staff_id, role="assistant")]),
        )
        results["b"] = [(st.staff_user_id, st.role) for st in out.staff]

    assert _run_blocked_pair(hold, blocked, db_engine) == []
    assert results["b"] == [(staff_id, "assistant")]
    with Session(bind=db_engine) as check:
        rows = (
            check.execute(select(ClassStaff.role).where(ClassStaff.class_id == class_id))
            .scalars()
            .all()
        )
    assert rows == ["assistant"]

    # 封存進行中不可改負責員工
    s1 = Session(bind=db_engine)
    s2 = Session(bind=db_engine)
    try:
        archive_class(s1, class_id, clock=fake_clock)
        _short_lock_timeout(s2)
        with pytest.raises(OperationalError) as locked:
            set_class_staff(s2, class_id, ClassStaffPutIn(items=[]))
        assert getattr(locked.value.orig, "sqlstate", None) == _LOCK_NOT_AVAILABLE
        s2.rollback()
        s1.commit()
        with pytest.raises(AppError) as exc:
            set_class_staff(s2, class_id, ClassStaffPutIn(items=[]))
        assert exc.value.code == "class_archived"
        s2.rollback()
    finally:
        s1.close()
        s2.close()
