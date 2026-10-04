"""BACKEND-075：app/services/rbac_guards.py（assert_admin_capabilities_retained）。

角色 / 員工權限異動之後、commit 之前呼叫：所有啟用員工的有效權限中必須仍有人持有 roles:write 與
staff:write（以帳號為準，停用帳號不算）。測試先把 DB 既有的啟用帳號全部停用（交易內，結束時
rollback）。
"""

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models.account import StaffUser
from app.services.rbac_guards import assert_admin_capabilities_retained
from tests.support.factories import make_staff


def _deactivate_everyone(db_session: Session) -> None:
    db_session.execute(update(StaffUser).values(is_active=False))


def test_retained_ok_with_admin(db_session: Session) -> None:
    _deactivate_everyone(db_session)
    make_staff(db_session, role_code="admin")

    # 不拋例外即通過
    assert_admin_capabilities_retained(db_session)


def test_retained_last_admin_deactivated(db_session: Session) -> None:
    _deactivate_everyone(db_session)
    admin = make_staff(db_session, role_code="admin")
    assert_admin_capabilities_retained(db_session)

    admin.is_active = False
    db_session.flush()

    with _raises_409("last_role_manager"):
        assert_admin_capabilities_retained(db_session)

    # 重新啟用（尚未 flush：query 會 autoflush）→ 通過
    admin.is_active = True
    assert_admin_capabilities_retained(db_session)


def test_retained_missing_staff_write(db_session: Session) -> None:
    _deactivate_everyone(db_session)
    make_staff(db_session, permissions=["roles:write"])

    with _raises_409("last_staff_manager"):
        assert_admin_capabilities_retained(db_session)


def test_retained_missing_roles_write_checked_first(db_session: Session) -> None:
    """兩者皆缺時先報 last_role_manager；只缺 roles:write 時報 last_role_manager。"""
    _deactivate_everyone(db_session)
    with _raises_409("last_role_manager"):
        assert_admin_capabilities_retained(db_session)

    make_staff(db_session, permissions=["staff:write"])
    with _raises_409("last_role_manager"):
        assert_admin_capabilities_retained(db_session)


def test_retained_counts_extra_and_revoked(db_session: Session) -> None:
    """有效權限含個別加減碼：extra 補上的算、revoked 拿掉的不算；可分散在兩位帳號。"""
    _deactivate_everyone(db_session)
    make_staff(db_session, permissions=["roles:read"], extra_permissions=["roles:write"])
    make_staff(db_session, permissions=["staff:write"])
    assert_admin_capabilities_retained(db_session)

    make_staff(
        db_session,
        permissions=["roles:write", "staff:write"],
        revoked_permissions=["roles:write", "staff:write"],
    )
    assert_admin_capabilities_retained(db_session)

    # 把持有 staff:write 的帳號停用 → 只剩 revoked 掉的那位，不算
    db_session.execute(
        update(StaffUser).where(StaffUser.revoked_permissions == []).values(is_active=False)
    )
    with _raises_409("last_role_manager"):
        assert_admin_capabilities_retained(db_session)


class _raises_409:
    """pytest.raises 風格：離開 with 時斷言拋出 AppError 409 且 code 相符。"""

    def __init__(self, code: str) -> None:
        self.code = code

    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        assert isinstance(exc, AppError), f"預期 AppError 409 {self.code}，實際 {exc!r}"
        assert exc.status == 409
        assert exc.code == self.code
        assert exc.message
        return True
