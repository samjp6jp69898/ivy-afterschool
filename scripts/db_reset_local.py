#!/usr/bin/env python3
"""INFRA-046：重建本機 DB（just db-reset）。

只操作 compose.yaml 的本機 Postgres（127.0.0.1:54342），不接受任何參數、不提供連遠端的路徑
（docs/architecture_decisions.md §5、docs/testing_conventions.md §5）：

1. reset_database：連 template1，`drop database if exists postgres with (force)`
   → `create database postgres`
2. upgrade_head：子行程 `uv run --frozen --project apps/api alembic upgrade head`
   （MIGRATION_DATABASE_URL 指向本機，並移除 libpq 會用來改寫連線目標的 PG* 環境變數）
3. set_local_backend_password：`alter role app_backend login password 'app_backend_local'`

連線前以 apps/api/tests/support/db_urls.py（INFRA-041）依 libpq 規則檢查 loopback，連線後以
conn.info.hostaddr 複驗；db_urls 以 importlib 依檔案路徑載入（與 scripts/tests/conftest.py 相同）。

exit code：0 成功；1 本機 DB 未啟動或步驟失敗；2 參數錯誤或連線目標不是本機 loopback。
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import psycopg
from psycopg import sql

REPO_ROOT = Path(__file__).resolve().parents[1]
API_DIR = REPO_ROOT / "apps" / "api"
LOCAL_OWNER_URL = "postgresql://postgres:postgres@127.0.0.1:54342/postgres"
# 與 apps/api/.env.example、tests/support/db_urls.py 的預設值一致
LOCAL_BACKEND_PASSWORD = "app_backend_local"  # noqa: S105  本機固定開發值，只對 127.0.0.1:54342 有效
LOCAL_DB_ADDRESS = ("127.0.0.1", 54342)
TARGET_DBNAME = "postgres"
# libpq 會以這些變數補上 URL 未指定的參數，可把連線導向他處；子行程一律移除
_LIBPQ_REDIRECT_ENV = ("PGHOST", "PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE")


def _load_db_urls() -> ModuleType:
    path = API_DIR / "tests" / "support" / "db_urls.py"
    spec = importlib.util.spec_from_file_location("_afterschool_db_urls", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"無法載入 {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_db_urls = _load_db_urls()


def _connect(owner_url: str, dbname: str) -> psycopg.Connection[Any]:
    """先檢查 URL（含 PGHOSTADDR 等環境變數）再連線，連線後以實際位址複驗。"""
    _db_urls.assert_loopback(owner_url)
    conn = psycopg.connect(
        _db_urls.to_psycopg_dsn(owner_url), dbname=dbname, autocommit=True, connect_timeout=5
    )
    try:
        _db_urls.assert_connected_loopback(conn.info.hostaddr)
    except ValueError:
        conn.close()
        raise
    return conn


def _with_dbname(url: str, dbname: str) -> str:
    base, _, _ = _db_urls.to_psycopg_dsn(url).rpartition("/")
    return f"{base}/{dbname}"


def reset_database(dbname: str, *, owner_url: str = LOCAL_OWNER_URL) -> None:
    with _connect(owner_url, "template1") as conn:
        name = sql.Identifier(dbname)
        conn.execute(sql.SQL("drop database if exists {} with (force)").format(name))
        conn.execute(sql.SQL("create database {}").format(name))


def upgrade_head(dbname: str, *, owner_url: str = LOCAL_OWNER_URL) -> str:
    env = {k: v for k, v in os.environ.items() if k not in _LIBPQ_REDIRECT_ENV}
    # 以子行程實際拿到的環境檢查（PG* 已移除）
    _db_urls.assert_loopback(owner_url, env)
    env["MIGRATION_DATABASE_URL"] = _db_urls.to_sqlalchemy_url(_with_dbname(owner_url, dbname))
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("找不到 uv，請先安裝（見 README）")
    result = subprocess.run(  # noqa: S603  固定參數呼叫 uv
        [
            uv,
            "run",
            "--frozen",
            "--project",
            str(API_DIR),
            "alembic",
            "-c",
            str(API_DIR / "alembic.ini"),
            "upgrade",
            "head",
        ],
        cwd=API_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        code = result.returncode
        raise RuntimeError(f"alembic upgrade head 失敗（exit {code}）：{result.stderr}")
    with _connect(owner_url, dbname) as conn:
        row = conn.execute("select version_num from alembic_version").fetchone()
    if row is None:
        raise RuntimeError("alembic upgrade head 後 alembic_version 為空")
    return str(row[0])


def set_local_backend_password(*, owner_url: str = LOCAL_OWNER_URL) -> None:
    with _connect(owner_url, TARGET_DBNAME) as conn:
        exists = conn.execute("select 1 from pg_roles where rolname = 'app_backend'").fetchone()
        if exists is None:
            raise RuntimeError("app_backend 不存在，baseline revision（DB-041）未套用")
        conn.execute(
            sql.SQL("alter role app_backend login password {}").format(
                sql.Literal(LOCAL_BACKEND_PASSWORD)
            )
        )


def local_db_reachable() -> bool:
    try:
        socket.create_connection(LOCAL_DB_ADDRESS, timeout=1).close()
    except OSError:
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args:
        print(f"錯誤：不接受任何參數，收到 {args}（只重置本機 DB）", file=sys.stderr)
        return 2
    if not local_db_reachable():
        print("錯誤：本機 DB 未啟動，先跑 just db-start", file=sys.stderr)
        return 1
    try:
        print(f"==> 重建 database {TARGET_DBNAME}（127.0.0.1:54342）")
        reset_database(TARGET_DBNAME)
        print("==> alembic upgrade head")
        head = upgrade_head(TARGET_DBNAME)
        print("==> 設定 app_backend 本機密碼")
        set_local_backend_password()
    except ValueError as exc:
        print(f"錯誤：{exc}", file=sys.stderr)
        return 2
    except (RuntimeError, psycopg.Error) as exc:
        print(f"錯誤：{exc}", file=sys.stderr)
        return 1
    print(f"本機 DB 已重建：alembic head = {head}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
