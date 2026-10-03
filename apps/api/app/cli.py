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
from typing import Final, TextIO

from app.core.migrate import (
    MigrationConfigError,
    load_migration_urls,
    run_migrations,
    scrub,
    secret_fragments,
)

ALEMBIC_INI: Final = Path(__file__).resolve().parents[1] / "alembic.ini"


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
