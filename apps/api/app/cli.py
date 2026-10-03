"""後端一次性指令入口：``python -m app.cli <子指令>``。

- ``migrate``（BACKEND-535）：Railway pre-deploy 套用 migration 並同步 app_backend 密碼。
- ``create-admin`` 由 BACKEND-065 加入。

exit code：0 成功、1 執行失敗（含等鎖逾時）、2 設定錯誤或參數錯誤。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Final

import psycopg
from psycopg.conninfo import conninfo_to_dict

from app.core.migrate import (
    MigrationConfigError,
    MigrationUrls,
    load_migration_urls,
    run_migrations,
)

ALEMBIC_INI: Final = Path(__file__).resolve().parents[1] / "alembic.ini"


def _passwords(urls: MigrationUrls) -> list[str]:
    found: list[str] = []
    for url in (urls.migration_url, urls.backend_url):
        try:
            params = conninfo_to_dict(url.replace("postgresql+psycopg://", "postgresql://", 1))
        except psycopg.Error:  # 解析失敗就沒有可遮罩的密碼
            continue
        value = params.get("password")
        if value:
            found.append(str(value))
    return found


def _scrub(text: str, secrets: list[str]) -> str:
    """例外訊息意外帶出連線字串時，把密碼換成 ***。"""
    for secret in sorted(secrets, key=len, reverse=True):
        text = text.replace(secret, "***")
    return text


def _migrate() -> int:
    try:
        urls = load_migration_urls(os.environ)
    except MigrationConfigError as exc:
        print(f"migration 設定錯誤：{exc}", file=sys.stderr)
        return 2
    try:
        head = run_migrations(urls, alembic_ini=ALEMBIC_INI)
    except MigrationConfigError as exc:
        print(f"migration 設定錯誤：{_scrub(str(exc), _passwords(urls))}", file=sys.stderr)
        return 2
    except Exception as exc:
        message = _scrub(str(exc), _passwords(urls))
        print(f"migration 失敗：{type(exc).__name__}: {message}", file=sys.stderr)
        return 1
    print(f"migration 完成：head = {head}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="afterschool 後端指令")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("migrate", help="套用 Alembic migration 並同步 app_backend 密碼")
    args = parser.parse_args(argv)
    if args.command == "migrate":
        return _migrate()
    parser.error(f"未知的子指令：{args.command}")
    return 2  # parser.error 會 SystemExit；這行只為型別檢查


if __name__ == "__main__":
    raise SystemExit(main())
