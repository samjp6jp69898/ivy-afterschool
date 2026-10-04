"""後端一次性指令入口：``python -m app.cli <子指令>``。

- ``migrate``（BACKEND-535）：Railway pre-deploy 套用 migration 並同步 app_backend 密碼。
- ``create-admin``（BACKEND-065）：一次性建立初始 admin 帳號（不寫在任何 migration）。

exit code：0 成功、1 執行失敗（含等鎖逾時）、2 設定錯誤或參數錯誤。
"""

from __future__ import annotations

import argparse
import getpass
import os
import re
import sys
from pathlib import Path
from typing import Final, TextIO

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import session_scope
from app.core.errors import AppError, ConflictError
from app.core.migrate import (
    MigrationConfigError,
    load_migration_urls,
    run_migrations,
    scrub,
    secret_fragments,
)
from app.core.security.passwords import hash_password, validate_password_strength
from app.models.account import Role, StaffUser
from app.services import audit_service

ALEMBIC_INI: Final = Path(__file__).resolve().parents[1] / "alembic.ini"
USERNAME_PATTERN: Final = re.compile(r"[a-z0-9][a-z0-9._-]{2,31}")


def _emit(text: str, stream: TextIO) -> None:
    """所有輸出先遮罩兩個 URL 的密碼片段（例外訊息意外帶出連線字串時也不洩漏到部署 log）。"""
    print(scrub(text, secret_fragments(os.environ)), file=stream)


def _migrate() -> int:
    try:
        urls = load_migration_urls(os.environ)
        head = run_migrations(urls, alembic_ini=ALEMBIC_INI)
    except MigrationConfigError as exc:
        _emit(f"migration 設定錯誤：{exc}", sys.stderr)
        return 2
    except Exception as exc:
        _emit(f"migration 失敗：{type(exc).__name__}: {exc}", sys.stderr)
        return 1
    _emit(f"migration 完成：head = {head}", sys.stdout)
    return 0


def create_admin(session: Session, *, username: str, display_name: str, password: str) -> StaffUser:
    """建立初始 admin（admin 系統角色、must_change_password）；已有啟用中的 admin 時拒絕。

    所有驗證都在寫入前完成；只 flush，commit 由呼叫端的 session_scope 負責。
    """
    username = username.strip().lower()
    if USERNAME_PATTERN.fullmatch(username) is None:
        raise AppError(
            "invalid_username",
            "帳號格式不正確（3~32 字元，小寫英數與 . _ -，需以英數開頭）",
            status=422,
        )
    validate_password_strength(password, username=username)

    admin_role = session.execute(select(Role).where(Role.code == "admin")).scalar_one_or_none()
    if admin_role is None:
        raise RuntimeError("找不到 admin 系統角色，請先套用 migration")
    active_admins = session.execute(
        select(func.count())
        .select_from(StaffUser)
        .where(StaffUser.role_id == admin_role.id, StaffUser.is_active.is_(True))
    ).scalar_one()
    if active_admins:
        raise ConflictError("admin_exists", "已存在啟用中的 admin 帳號，請改由後台建立")
    if session.execute(select(StaffUser.id).where(StaffUser.username == username)).first():
        raise ConflictError("username_taken", "帳號已被使用")

    staff = StaffUser(
        username=username,
        password_hash=hash_password(password),
        display_name=display_name,
        role=admin_role,
        must_change_password=True,
    )
    session.add(staff)
    session.flush()
    audit_service.record(
        session,
        actor=audit_service.Actor.system(),
        action="staff_user.create",
        entity_type="staff_user",
        entity_id=staff.id,
        after={"username": username, "display_name": display_name, "role": "admin"},
    )
    return staff


def _create_admin(username: str, display_name: str) -> int:
    password = getpass.getpass("密碼：")
    if getpass.getpass("再輸入一次密碼：") != password:
        print("兩次輸入不一致", file=sys.stderr)
        return 2
    try:
        with session_scope() as session:
            staff = create_admin(
                session, username=username, display_name=display_name, password=password
            )
            created = staff.username
    except AppError as exc:
        # 422（輸入不合格）→ 2；409（已有 admin、帳號重複）→ 1。訊息不含密碼
        print(exc.message, file=sys.stderr)
        return 2 if exc.status == 422 else 1
    except Exception as exc:
        print(f"建立失敗：{type(exc).__name__}", file=sys.stderr)
        return 1
    print(f"已建立 admin 帳號：{created}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="afterschool 後端指令")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("migrate", help="套用 Alembic migration 並同步 app_backend 密碼")
    admin = subcommands.add_parser("create-admin", help="建立初始 admin 帳號（互動輸入密碼）")
    admin.add_argument("--username", required=True)
    admin.add_argument("--display-name", required=True)
    args = parser.parse_args(argv)
    if args.command == "migrate":
        return _migrate()
    if args.command == "create-admin":
        return _create_admin(args.username, args.display_name)
    parser.error(f"未知的子指令：{args.command}")
    return 2  # parser.error 會 SystemExit；這行只為型別檢查


if __name__ == "__main__":
    raise SystemExit(main())
