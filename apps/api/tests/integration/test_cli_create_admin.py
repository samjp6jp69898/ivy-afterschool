"""BACKEND-065：python -m app.cli create-admin（一次性建立初始 admin）。"""

from __future__ import annotations

import getpass
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import cli
from app.cli import create_admin, main
from app.core.errors import AppError
from app.core.security.passwords import verify_password
from app.models.account import StaffUser
from app.models.audit import AuditLog
from tests.support.factories import make_staff

_PASSWORD = "Afterschool2026"


def _staff_count(db: Session) -> int:
    return db.execute(select(func.count()).select_from(StaffUser)).scalar_one()


@pytest.fixture
def bound_session(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> Session:
    """main() 的 session_scope 綁到 db_session（不 commit，測試結束整筆 rollback）。"""

    @contextmanager
    def _scope() -> Iterator[Session]:
        yield db_session

    monkeypatch.setattr(cli, "session_scope", _scope)
    return db_session


def _answers(monkeypatch: pytest.MonkeyPatch, *answers: str) -> None:
    queue = list(answers)
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": queue.pop(0))


_ARGV = ["create-admin", "--username", "x2", "--display-name", "X"]


def test_cli_create_admin_success(db_session: Session) -> None:
    staff = create_admin(db_session, username="Owner", display_name="負責人", password=_PASSWORD)

    assert staff.username == "owner"
    assert staff.display_name == "負責人"
    assert staff.role.code == "admin"
    assert staff.must_change_password is True
    assert staff.is_active is True
    assert verify_password(_PASSWORD, staff.password_hash)
    log = db_session.execute(
        select(AuditLog).where(
            AuditLog.action == "staff_user.create", AuditLog.entity_id == str(staff.id)
        )
    ).scalar_one()
    assert log.actor_type == "system"
    assert log.actor_id is None
    assert log.entity_type == "staff_user"
    assert log.after == {"username": "owner", "display_name": "負責人", "role": "admin"}
    assert _PASSWORD not in str(log.after)


def test_cli_create_admin_main_success(
    bound_session: Session, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _answers(monkeypatch, _PASSWORD, _PASSWORD)
    before = _staff_count(bound_session)

    code = main(_ARGV)

    assert code == 0
    assert _staff_count(bound_session) == before + 1
    assert "x2" in capsys.readouterr().out


def test_cli_create_admin_refuses_when_admin_exists(
    bound_session: Session, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    make_staff(bound_session, role_code="admin")
    bound_session.flush()
    before = _staff_count(bound_session)
    _answers(monkeypatch, _PASSWORD, _PASSWORD)

    code = main(_ARGV)

    assert code == 1
    assert _staff_count(bound_session) == before
    assert "已存在啟用中的 admin 帳號，請改由後台建立" in capsys.readouterr().err


def test_cli_create_admin_allows_when_only_inactive_admin(
    bound_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_staff(bound_session, role_code="admin", is_active=False)
    bound_session.flush()
    _answers(monkeypatch, _PASSWORD, _PASSWORD)

    assert main(_ARGV) == 0


def test_cli_create_admin_password_mismatch(
    bound_session: Session, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    before = _staff_count(bound_session)
    _answers(monkeypatch, "Afterschool2026", "Afterschool2027")

    code = main(_ARGV)

    assert code == 2
    assert _staff_count(bound_session) == before
    assert "兩次輸入不一致" in capsys.readouterr().err


def test_cli_create_admin_weak_password(
    bound_session: Session, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    before = _staff_count(bound_session)
    _answers(monkeypatch, "abc", "abc")

    code = main(_ARGV)

    assert code != 0
    assert _staff_count(bound_session) == before
    assert "密碼強度不足" in capsys.readouterr().err


@pytest.mark.parametrize("username", ["ab", "-bad", "has space", "x" * 33, "中文帳號"])
def test_cli_create_admin_bad_username(
    bound_session: Session, monkeypatch: pytest.MonkeyPatch, username: str
) -> None:
    before = _staff_count(bound_session)
    _answers(monkeypatch, _PASSWORD, _PASSWORD)

    code = main(["create-admin", "--username", username, "--display-name", "X"])

    assert code != 0
    assert _staff_count(bound_session) == before


def test_cli_create_admin_duplicate_username(
    bound_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_staff(bound_session, username="taken")
    bound_session.flush()
    before = _staff_count(bound_session)
    _answers(monkeypatch, _PASSWORD, _PASSWORD)

    code = main(["create-admin", "--username", "TAKEN", "--display-name", "X"])

    assert code != 0
    assert _staff_count(bound_session) == before


def test_cli_create_admin_missing_args() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["create-admin", "--username", "owner"])

    assert exc.value.code == 2


def test_cli_create_admin_function_rejects_weak_password(db_session: Session) -> None:
    with pytest.raises(AppError) as exc:
        create_admin(db_session, username="owner", display_name="負責人", password="abc")

    assert exc.value.code == "weak_password"


def test_cli_create_admin_no_password_in_output(
    bound_session: Session, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _answers(monkeypatch, _PASSWORD, _PASSWORD)

    assert main(_ARGV) == 0

    captured = capsys.readouterr()
    assert _PASSWORD not in captured.out
    assert _PASSWORD not in captured.err
