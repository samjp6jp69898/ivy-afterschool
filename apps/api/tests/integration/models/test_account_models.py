"""BACKEND-030：app/models/account.py（Role、StaffUser、RefreshToken）。

以 app_backend 的 db_session 實際寫入 / 讀回，驗 server default、joined 載入與自參照 FK；
欄位與 migration 的一致性由 BACKEND-024 的 drift 測試把關。
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.account import RefreshToken, Role, StaffUser

# 合法格式的 argon2id 編碼字串（假值，不對應任何密碼）
_ARGON2ID_HASH = "$argon2id$v=19$m=65536,t=3,p=4$abc$def"


def _make_staff(db_session: Session) -> StaffUser:
    role = Role(code="probe_role", name="測試", permissions=["students:read"])
    staff = StaffUser(
        username="lin.teacher", password_hash=_ARGON2ID_HASH, display_name="林老師", role=role
    )
    db_session.add_all([role, staff])
    db_session.flush()
    return staff


def test_account_models_staff_defaults(db_session: Session) -> None:
    staff = _make_staff(db_session)
    db_session.refresh(staff)

    assert isinstance(staff.id, UUID)
    assert isinstance(staff.role_id, UUID)
    assert staff.role_id == staff.role.id
    assert staff.token_version == 0
    assert staff.must_change_password is True
    assert staff.is_active is True
    assert staff.extra_permissions == []
    assert staff.revoked_permissions == []
    assert staff.last_login_at is None
    assert staff.created_at.tzinfo is not None
    assert staff.role.is_system is False
    assert staff.role.permissions == ["students:read"]


def test_account_models_role_joined(db_session: Session) -> None:
    staff_id = _make_staff(db_session).id
    # 清掉 identity map，確保下面的查詢真的從 DB 讀回而不是拿到剛建立的物件
    db_session.expunge_all()

    staff = db_session.execute(select(StaffUser).where(StaffUser.id == staff_id)).scalar_one()

    assert StaffUser.role.property.lazy == "joined"
    # joined 載入：查回時 role 已在物件上，之後存取不再發查詢
    assert "role" not in inspect(staff).unloaded
    assert staff.role.code == "probe_role"
    assert staff.role.name == "測試"
    # 反向關係 lazy='raise'：不得在未明確載入時觸發 N+1
    assert Role.staff_users.property.lazy == "raise"


def test_account_models_refresh_token_replaced_by(db_session: Session) -> None:
    subject_id = uuid4()
    family_id = uuid4()
    expires_at = datetime(2099, 1, 1, tzinfo=UTC)
    first = RefreshToken(
        subject_type="staff",
        subject_id=subject_id,
        family_id=family_id,
        token_hash="a" * 64,
        expires_at=expires_at,
    )
    second = RefreshToken(
        subject_type="staff",
        subject_id=subject_id,
        family_id=family_id,
        token_hash="b" * 64,
        expires_at=expires_at,
    )
    db_session.add_all([first, second])
    db_session.flush()
    first.replaced_by = second.id
    db_session.flush()
    db_session.expunge_all()

    loaded = db_session.execute(
        select(RefreshToken).where(RefreshToken.token_hash == "a" * 64)
    ).scalar_one()
    assert loaded.replaced_by == second.id
    assert loaded.revoked_at is None
    assert loaded.subject_type == "staff"
    assert loaded.expires_at == expires_at

    db_session.add(
        RefreshToken(
            subject_type="admin",
            subject_id=subject_id,
            family_id=family_id,
            token_hash="c" * 64,
            expires_at=expires_at,
        )
    )
    with pytest.raises(IntegrityError) as excinfo:
        db_session.flush()
    assert excinfo.value.orig is not None
    assert getattr(excinfo.value.orig, "sqlstate", None) == "23514"
    db_session.rollback()
