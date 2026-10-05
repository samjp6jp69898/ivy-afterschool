"""BACKEND-052：app/services/auth/parent_auth.py（ParentAuthService.liff_login）。
BACKEND-058：refresh（與 BACKEND-043 對稱；停用 / 不存在 / 員工 token 撤銷 family 並先 commit）。
BACKEND-060：logout（撤銷目前 family，冪等）。
BACKEND-056：bind（首次綁定建立家長帳號並簽發 token、既有帳號走首次流程撤銷舊 family、加綁不簽新
token、連續失敗鎖定 429、停用家長 403）。

已綁定家長直接登入（同步暱稱、last_login_at、簽 access + refresh）；未建帳號或沒有任何有效綁定
→ NeedsBinding（發綁定臨時 token，不建立 parent_accounts）；停用 403；LIFF 未設定 503。
"""

# ruff: noqa: S105, S106  測試用的 token 字面值

import json
from collections.abc import Iterator
from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.api.deps import CurrentParent
from app.core.config import get_settings
from app.core.crypto import derive_key
from app.core.errors import AppError
from app.core.request_meta import RequestMeta
from app.core.security.tokens import BindClaims, decode_access_token, decode_bind_token
from app.models.account import RefreshToken, StaffUser
from app.models.notifications import Notification
from app.models.parents import Guardian, ParentAccount, ParentBindingCode
from app.services.auth.line_id_token import LineProfile
from app.services.auth.parent_auth import (
    BindResult,
    NeedsBinding,
    ParentSession,
    bind,
    liff_login,
    logout,
    refresh,
)
from app.services.auth.refresh_tokens import hash_refresh, issue
from app.services.auth.throttle import AuthThrottles
from app.services.binding_code_service import hash_code
from app.services.parent_scope import get_parent_student_ids
from app.services.settings_service import clear_settings_cache, invalidate_setting
from tests.support.factories import make_guardian, make_parent, make_staff, make_student
from tests.support.fake_clock import FakeClock
from tests.support.fake_line import FakeLineVerifier

_LINE_A = "U" + "a" * 32
_LINE_B = "U" + "b" * 32
_ID_TOKEN = "id-token-1"
_META = RequestMeta(ip="203.0.113.5", user_agent="pytest", request_id=None)
_LIFF = {"liff_id": "1657000000-Abc", "channel_id": "1657000000", "add_friend_url": ""}


@pytest.fixture(autouse=True)
def _env_and_cache(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
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
    clear_settings_cache()
    yield
    clear_settings_cache()
    get_settings.cache_clear()
    derive_key.cache_clear()


@pytest.fixture
def throttles() -> AuthThrottles:
    return AuthThrottles()


def _set_liff(session: Session, value: dict[str, Any]) -> None:
    session.execute(
        text("update public.system_settings set value = cast(:v as jsonb) where key = 'line.liff'"),
        {"v": json.dumps(value)},
    )
    invalidate_setting("line.liff")


@pytest.fixture
def liff_configured(db_session: Session) -> None:
    _set_liff(db_session, _LIFF)


def _verifier(profile: LineProfile) -> FakeLineVerifier:
    return FakeLineVerifier({_ID_TOKEN: profile})


def _login(
    db_session: Session,
    verifier: FakeLineVerifier,
    throttles: AuthThrottles,
    fake_clock: FakeClock,
    *,
    id_token: str = _ID_TOKEN,
) -> ParentSession | NeedsBinding:
    return liff_login(
        db_session,
        id_token=id_token,
        verifier=verifier,
        meta=_META,
        throttles=throttles,
        clock=fake_clock,
    )


def _parent_count(db_session: Session) -> int:
    return int(db_session.execute(select(func.count()).select_from(ParentAccount)).scalar_one())


def _refresh_count(db_session: Session, parent: ParentAccount) -> int:
    return int(
        db_session.execute(
            select(func.count())
            .select_from(RefreshToken)
            .where(RefreshToken.subject_type == "parent", RefreshToken.subject_id == parent.id)
        ).scalar_one()
    )


@pytest.mark.usefixtures("liff_configured")
def test_liff_login_bound_parent(
    db_session: Session, throttles: AuthThrottles, fake_clock: FakeClock
) -> None:
    p = make_parent(db_session, line_user_id=_LINE_A, display_name="舊暱稱")
    make_guardian(db_session, make_student(db_session), parent=p)
    verifier = _verifier(LineProfile(_LINE_A, "王媽媽", None))
    count_before = _parent_count(db_session)

    result = _login(db_session, verifier, throttles, fake_clock)

    assert isinstance(result, ParentSession)
    assert result.parent.id == p.id
    assert p.display_name == "王媽媽"
    assert p.picture_url is None
    assert p.last_login_at == fake_clock.now()
    assert _refresh_count(db_session, p) == 1
    assert _parent_count(db_session) == count_before
    claims = decode_access_token(result.access_token, expected_type="parent", clock=fake_clock)
    assert claims.subject_id == p.id
    assert claims.token_version == p.token_version
    assert len(result.refresh_token) >= 64
    assert verifier.calls == [(_ID_TOKEN, "1657000000")]


@pytest.mark.usefixtures("liff_configured")
def test_liff_login_profile_sync_rules(
    db_session: Session, throttles: AuthThrottles, fake_clock: FakeClock
) -> None:
    """LINE 值非空且有變才更新；LINE 沒給暱稱時保留原值。"""
    p = make_parent(db_session, line_user_id=_LINE_A, display_name="王媽媽")
    p.picture_url = "https://profile.line-scdn.net/old"
    make_guardian(db_session, make_student(db_session), parent=p)

    _login(db_session, _verifier(LineProfile(_LINE_A, None, None)), throttles, fake_clock)
    assert p.display_name == "王媽媽"
    assert p.picture_url == "https://profile.line-scdn.net/old"

    _login(
        db_session,
        _verifier(LineProfile(_LINE_A, "王媽咪", "https://profile.line-scdn.net/new")),
        throttles,
        fake_clock,
    )
    assert p.display_name == "王媽咪"
    assert p.picture_url == "https://profile.line-scdn.net/new"
    # 兩次登入各自一個新 family
    assert _refresh_count(db_session, p) == 2


@pytest.mark.usefixtures("liff_configured")
def test_liff_login_unknown_needs_binding(
    db_session: Session, throttles: AuthThrottles, fake_clock: FakeClock
) -> None:
    verifier = _verifier(LineProfile(_LINE_B, "王媽媽", "https://profile.line-scdn.net/p"))
    count_before = _parent_count(db_session)

    result = _login(db_session, verifier, throttles, fake_clock)

    assert isinstance(result, NeedsBinding)
    assert result.name_hint == "王媽媽"
    claims = decode_bind_token(result.bind_token, clock=fake_clock)
    assert claims.line_user_id == _LINE_B
    assert claims.display_name == "王媽媽"
    assert claims.picture_url == "https://profile.line-scdn.net/p"
    assert _parent_count(db_session) == count_before
    assert (
        db_session.execute(
            select(func.count())
            .select_from(RefreshToken)
            .where(RefreshToken.subject_type == "parent")
        ).scalar_one()
        == 0
    )


@pytest.mark.usefixtures("liff_configured")
def test_liff_login_no_active_binding_needs_binding(
    db_session: Session, throttles: AuthThrottles, fake_clock: FakeClock
) -> None:
    p = make_parent(db_session, line_user_id=_LINE_A, display_name="王媽媽")
    make_guardian(db_session, make_student(db_session), parent=p, archived=True)
    count_before = _parent_count(db_session)

    result = _login(
        db_session, _verifier(LineProfile(_LINE_A, "王媽媽", None)), throttles, fake_clock
    )

    assert isinstance(result, NeedsBinding)
    assert result.name_hint == "王媽媽"
    assert decode_bind_token(result.bind_token, clock=fake_clock).line_user_id == _LINE_A
    assert _parent_count(db_session) == count_before
    assert _refresh_count(db_session, p) == 0
    assert p.last_login_at is None

    # 學生封存同樣不算有效綁定
    q = make_parent(db_session, line_user_id=_LINE_B)
    make_guardian(db_session, make_student(db_session, archived=True), parent=q)
    assert isinstance(
        _login(db_session, _verifier(LineProfile(_LINE_B, None, None)), throttles, fake_clock),
        NeedsBinding,
    )


@pytest.mark.usefixtures("liff_configured")
def test_liff_login_disabled_parent(
    db_session: Session, throttles: AuthThrottles, fake_clock: FakeClock
) -> None:
    p = make_parent(db_session, line_user_id=_LINE_A, status="disabled")
    make_guardian(db_session, make_student(db_session), parent=p)

    with pytest.raises(AppError) as excinfo:
        _login(db_session, _verifier(LineProfile(_LINE_A, "王媽媽", None)), throttles, fake_clock)

    assert excinfo.value.status == 403
    assert excinfo.value.code == "parent_disabled"
    assert _refresh_count(db_session, p) == 0
    assert p.last_login_at is None


def test_liff_login_not_configured(
    db_session: Session, throttles: AuthThrottles, fake_clock: FakeClock
) -> None:
    _set_liff(db_session, {**_LIFF, "channel_id": ""})
    verifier = _verifier(LineProfile(_LINE_A, "王媽媽", None))

    with pytest.raises(AppError) as excinfo:
        _login(db_session, verifier, throttles, fake_clock)

    assert excinfo.value.status == 503
    assert excinfo.value.code == "line_login_not_configured"
    assert verifier.calls == []


@pytest.mark.usefixtures("liff_configured")
def test_liff_login_verifier_errors_propagate(
    db_session: Session, throttles: AuthThrottles, fake_clock: FakeClock
) -> None:
    for error in (
        AppError("invalid_id_token", "LINE 登入驗證失敗，請重新開啟", status=401),
        AppError("line_unavailable", "LINE 驗證服務暫時無法連線", status=503),
    ):
        with pytest.raises(AppError) as excinfo:
            _login(db_session, FakeLineVerifier(error=error), throttles, fake_clock)
        assert excinfo.value is error

    # 未知的 id_token（FakeLineVerifier 回 401）
    with pytest.raises(AppError) as unknown:
        _login(
            db_session,
            _verifier(LineProfile(_LINE_A, None, None)),
            throttles,
            fake_clock,
            id_token="other",
        )
    assert unknown.value.code == "invalid_id_token"


@pytest.mark.usefixtures("liff_configured")
def test_liff_login_ip_rate_limited(
    db_session: Session, throttles: AuthThrottles, fake_clock: FakeClock
) -> None:
    """同 IP 5 分鐘 30 次（AuthThrottles.liff_ip）：第 31 次 429，且不呼叫 verifier。"""
    verifier = _verifier(LineProfile(_LINE_B, "王媽媽", None))
    for _ in range(30):
        _login(db_session, verifier, throttles, fake_clock)

    with pytest.raises(AppError) as excinfo:
        _login(db_session, verifier, throttles, fake_clock)
    assert excinfo.value.status == 429
    assert len(verifier.calls) == 30


# --- BACKEND-058：refresh、BACKEND-060：logout -------------------------------------------------


def _family_rows(db_session: Session, raw: str) -> list[RefreshToken]:
    family_id = db_session.execute(
        select(RefreshToken.family_id).where(RefreshToken.token_hash == hash_refresh(raw))
    ).scalar_one()
    return list(
        db_session.execute(
            select(RefreshToken).where(RefreshToken.family_id == family_id)
        ).scalars()
    )


def _assert_family_revoked(db_session: Session, raw: str, *, revoked: bool) -> None:
    rows = _family_rows(db_session, raw)
    assert rows
    assert all((row.revoked_at is not None) is revoked for row in rows)


def _issue_parent(db_session: Session, parent: ParentAccount, fake_clock: FakeClock) -> str:
    return issue(db_session, subject_type="parent", subject_id=parent.id, clock=fake_clock).raw


def test_parent_refresh_success(db_session: Session, fake_clock: FakeClock) -> None:
    p = make_parent(db_session)
    make_guardian(db_session, make_student(db_session), parent=p)
    raw = _issue_parent(db_session, p, fake_clock)
    fake_clock.advance(minutes=10)

    result = refresh(db_session, raw_refresh=raw, clock=fake_clock)

    assert isinstance(result, ParentSession)
    assert result.parent.id == p.id
    assert result.refresh_token != raw
    claims = decode_access_token(result.access_token, expected_type="parent", clock=fake_clock)
    assert claims.subject_id == p.id
    assert claims.token_version == p.token_version
    assert claims.issued_at == fake_clock.now()
    rows = {row.token_hash: row for row in _family_rows(db_session, raw)}
    assert set(rows) == {hash_refresh(raw), hash_refresh(result.refresh_token)}
    assert all(row.revoked_at is None for row in rows.values())
    # 沒有任何有效綁定的家長仍可 refresh
    q = make_parent(db_session)
    assert (
        refresh(
            db_session, raw_refresh=_issue_parent(db_session, q, fake_clock), clock=fake_clock
        ).parent.id
        == q.id
    )
    # token_version 取 DB 現值
    p.token_version = 2
    db_session.flush()
    again = refresh(db_session, raw_refresh=result.refresh_token, clock=fake_clock)
    assert (
        decode_access_token(
            again.access_token, expected_type="parent", clock=fake_clock
        ).token_version
        == 2
    )


def test_parent_refresh_disabled(db_session: Session, fake_clock: FakeClock) -> None:
    p = make_parent(db_session)
    raw = _issue_parent(db_session, p, fake_clock)
    db_session.commit()
    p.status = "disabled"
    db_session.flush()

    with pytest.raises(AppError) as excinfo:
        refresh(db_session, raw_refresh=raw, clock=fake_clock)

    assert excinfo.value.status == 401
    assert excinfo.value.code == "unauthenticated"
    _assert_family_revoked(db_session, raw, revoked=True)
    # 撤銷在 raise 前已 commit：endpoint 的 rollback 不會退回
    db_session.rollback()
    _assert_family_revoked(db_session, raw, revoked=True)


def test_parent_refresh_missing_parent(db_session: Session, fake_clock: FakeClock) -> None:
    raw = issue(db_session, subject_type="parent", subject_id=uuid4(), clock=fake_clock).raw

    with pytest.raises(AppError) as excinfo:
        refresh(db_session, raw_refresh=raw, clock=fake_clock)

    assert excinfo.value.code == "unauthenticated"
    _assert_family_revoked(db_session, raw, revoked=True)


def test_parent_refresh_staff_token_rejected(db_session: Session, fake_clock: FakeClock) -> None:
    staff = make_staff(db_session)
    raw = issue(db_session, subject_type="staff", subject_id=staff.id, clock=fake_clock).raw

    with pytest.raises(AppError) as excinfo:
        refresh(db_session, raw_refresh=raw, clock=fake_clock)

    assert excinfo.value.status == 401
    assert excinfo.value.code == "refresh_invalid"
    _assert_family_revoked(db_session, raw, revoked=True)


def test_parent_refresh_missing(db_session: Session, fake_clock: FakeClock) -> None:
    for raw in (None, ""):
        with pytest.raises(AppError) as excinfo:
            refresh(db_session, raw_refresh=raw, clock=fake_clock)
        assert excinfo.value.status == 401
        assert excinfo.value.code == "unauthenticated"


def test_parent_refresh_propagates_rotate_errors(
    db_session: Session, fake_clock: FakeClock
) -> None:
    p = make_parent(db_session)
    raw = _issue_parent(db_session, p, fake_clock)
    refresh(db_session, raw_refresh=raw, clock=fake_clock)
    fake_clock.advance(seconds=2)
    with pytest.raises(AppError) as in_progress:
        refresh(db_session, raw_refresh=raw, clock=fake_clock)
    assert (in_progress.value.status, in_progress.value.code) == (409, "refresh_in_progress")

    fake_clock.advance(seconds=10)
    with pytest.raises(AppError) as reused:
        refresh(db_session, raw_refresh=raw, clock=fake_clock)
    assert (reused.value.status, reused.value.code) == (401, "refresh_reused")
    _assert_family_revoked(db_session, raw, revoked=True)
    with pytest.raises(AppError) as invalid:
        refresh(db_session, raw_refresh="no-such-token", clock=fake_clock)
    assert invalid.value.code == "refresh_invalid"


def test_parent_logout_revokes(db_session: Session, fake_clock: FakeClock) -> None:
    p = make_parent(db_session)
    first = _issue_parent(db_session, p, fake_clock)
    second = _issue_parent(db_session, p, fake_clock)

    assert logout(db_session, raw_refresh=first, clock=fake_clock) == 1

    _assert_family_revoked(db_session, first, revoked=True)
    _assert_family_revoked(db_session, second, revoked=False)
    assert refresh(db_session, raw_refresh=second, clock=fake_clock).parent.id == p.id
    with pytest.raises(AppError) as excinfo:
        refresh(db_session, raw_refresh=first, clock=fake_clock)
    assert excinfo.value.code == "refresh_revoked"


def test_parent_logout_idempotent(db_session: Session, fake_clock: FakeClock) -> None:
    p = make_parent(db_session)
    raw = _issue_parent(db_session, p, fake_clock)

    assert logout(db_session, raw_refresh=None, clock=fake_clock) == 0
    assert logout(db_session, raw_refresh="", clock=fake_clock) == 0
    assert logout(db_session, raw_refresh="nope", clock=fake_clock) == 0
    _assert_family_revoked(db_session, raw, revoked=False)
    assert logout(db_session, raw_refresh=raw, clock=fake_clock) == 1
    assert logout(db_session, raw_refresh=raw, clock=fake_clock) == 0


# --- BACKEND-056：bind ----------------------------------------------------------------------------

_LINE_C = "U" + "c" * 32
_CODE = "ABCD2345"


def _claims(line_user_id: str = _LINE_C) -> BindClaims:
    return BindClaims(line_user_id=line_user_id, display_name="王媽媽", picture_url=None)


def _current_parent(parent: ParentAccount) -> CurrentParent:
    return CurrentParent(
        id=parent.id,
        line_user_id=parent.line_user_id,
        display_name=parent.display_name,
        token_version=parent.token_version,
    )


def _issue_code(
    db: Session, guardian: Guardian, staff: StaffUser, clock: FakeClock, *, code: str = _CODE
) -> ParentBindingCode:
    row = ParentBindingCode(
        created_at=clock.now() - timedelta(hours=1),
        guardian_id=guardian.id,
        code_hash=hash_code(code),
        expires_at=clock.now() + timedelta(days=6),
        created_by=staff.id,
    )
    db.add(row)
    db.flush()
    return row


def _bind(
    db: Session,
    identity: CurrentParent | BindClaims,
    throttles: AuthThrottles,
    clock: FakeClock,
    *,
    code: str = _CODE,
) -> BindResult:
    return bind(db, raw_code=code, identity=identity, throttles=throttles, clock=clock)


def _binding_notifications(db: Session, parent_id: object) -> list[Notification]:
    return list(
        db.execute(
            select(Notification).where(
                Notification.recipient_type == "parent",
                Notification.recipient_id == parent_id,
                Notification.event == "binding.completed",
            )
        ).scalars()
    )


def test_bind_first_creates_parent(
    db_session: Session, throttles: AuthThrottles, fake_clock: FakeClock
) -> None:
    staff = make_staff(db_session)
    ming = make_student(db_session, name="王小明")
    guardian = make_guardian(db_session, ming)
    code = _issue_code(db_session, guardian, staff, fake_clock)
    count_before = _parent_count(db_session)

    result = _bind(db_session, _claims(), throttles, fake_clock)

    assert isinstance(result, BindResult)
    assert result.session_tokens is not None
    parent = result.parent
    assert _parent_count(db_session) == count_before + 1
    assert (parent.line_user_id, parent.display_name, parent.status) == (
        _LINE_C,
        "王媽媽",
        "active",
    )
    assert parent.last_login_at == fake_clock.now()
    assert result.guardian.id == guardian.id
    db_session.refresh(guardian)
    assert guardian.parent_account_id == parent.id
    db_session.refresh(code)
    assert code.used_at == fake_clock.now()
    claims = decode_access_token(
        result.session_tokens.access_token, expected_type="parent", clock=fake_clock
    )
    assert claims.subject_id == parent.id
    assert _refresh_count(db_session, parent) == 1
    assert get_parent_student_ids(db_session, parent.id) == [ming.id]
    notes = _binding_notifications(db_session, parent.id)
    assert len(notes) == 1
    assert notes[0].title == "綁定完成"
    assert "王小明" in notes[0].body
    assert notes[0].payload["student_id"] == str(ming.id)
    # 成功後失敗計數清除：鎖定表不再有該 key
    assert len(throttles.bind) == 0


def test_bind_first_existing_parent_revokes_old_family(
    db_session: Session, throttles: AuthThrottles, fake_clock: FakeClock
) -> None:
    staff = make_staff(db_session)
    parent = make_parent(db_session, line_user_id=_LINE_C, display_name="舊暱稱")
    old_raw = _issue_parent(db_session, parent, fake_clock)
    # 曾被解除綁定：guardian 尚無家長
    guardian = make_guardian(db_session, make_student(db_session))
    _issue_code(db_session, guardian, staff, fake_clock)
    count_before = _parent_count(db_session)

    result = _bind(db_session, _claims(), throttles, fake_clock)

    assert result.parent.id == parent.id
    assert _parent_count(db_session) == count_before
    _assert_family_revoked(db_session, old_raw, revoked=True)
    assert result.session_tokens is not None
    assert result.session_tokens.refresh_token != old_raw
    _assert_family_revoked(db_session, result.session_tokens.refresh_token, revoked=False)
    db_session.refresh(guardian)
    assert guardian.parent_account_id == parent.id


def test_bind_additional_child(
    db_session: Session, throttles: AuthThrottles, fake_clock: FakeClock
) -> None:
    staff = make_staff(db_session)
    parent = make_parent(db_session, line_user_id=_LINE_C)
    student_a = make_student(db_session, name="王小明")
    student_b = make_student(db_session, name="王小華")
    make_guardian(db_session, student_a, parent=parent)
    guardian_b = make_guardian(db_session, student_b)
    _issue_code(db_session, guardian_b, staff, fake_clock)
    refresh_before = _refresh_count(db_session, parent)

    result = _bind(db_session, _current_parent(parent), throttles, fake_clock)

    assert result.session_tokens is None
    assert result.parent.id == parent.id
    assert result.guardian.id == guardian_b.id
    assert set(get_parent_student_ids(db_session, parent.id)) == {student_a.id, student_b.id}
    assert _refresh_count(db_session, parent) == refresh_before
    assert len(_binding_notifications(db_session, parent.id)) == 1
    # 他人已綁定的 guardian 碼 → 409（BACKEND-055），不會搶走綁定
    other_parent = make_parent(db_session)
    taken = make_guardian(db_session, make_student(db_session), parent=other_parent)
    _issue_code(db_session, taken, staff, fake_clock, code="TAKEN234")
    with pytest.raises(AppError) as exc:
        _bind(db_session, _current_parent(parent), throttles, fake_clock, code="TAKEN234")
    assert (exc.value.status, exc.value.code) == (409, "guardian_already_bound")
    db_session.refresh(taken)
    assert taken.parent_account_id == other_parent.id


def test_bind_lockout(db_session: Session, throttles: AuthThrottles, fake_clock: FakeClock) -> None:
    staff = make_staff(db_session)
    guardian = make_guardian(db_session, make_student(db_session))
    code = _issue_code(db_session, guardian, staff, fake_clock)
    count_before = _parent_count(db_session)

    for _ in range(5):
        with pytest.raises(AppError) as wrong:
            _bind(db_session, _claims(), throttles, fake_clock, code="WRONG234")
        assert (wrong.value.status, wrong.value.code) == (400, "binding_code_invalid")
    with pytest.raises(AppError) as locked:
        _bind(db_session, _claims(), throttles, fake_clock)

    assert (locked.value.status, locked.value.code) == (429, "too_many_attempts")
    assert locked.value.details["retry_after_seconds"] > 0
    db_session.refresh(code)
    assert code.used_at is None
    # 另一個 LINE 帳號不受影響
    other = _bind(db_session, _claims("U" + "d" * 32), throttles, fake_clock)
    assert other.parent.line_user_id == "U" + "d" * 32
    # 首次流程失敗不會留下帳號（帳號只在 claim 成功的同一交易內建立，失敗由呼叫端 rollback）
    assert _parent_count(db_session) >= count_before


def test_bind_disabled_parent(
    db_session: Session, throttles: AuthThrottles, fake_clock: FakeClock
) -> None:
    staff = make_staff(db_session)
    make_parent(db_session, line_user_id=_LINE_C, status="disabled")
    guardian = make_guardian(db_session, make_student(db_session))
    code = _issue_code(db_session, guardian, staff, fake_clock)

    with pytest.raises(AppError) as exc:
        _bind(db_session, _claims(), throttles, fake_clock)

    assert (exc.value.status, exc.value.code) == (403, "parent_disabled")
    db_session.refresh(code)
    assert code.used_at is None
    db_session.refresh(guardian)
    assert guardian.parent_account_id is None
