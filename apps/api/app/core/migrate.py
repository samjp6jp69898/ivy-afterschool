"""BACKEND-535：部署前套用 migration（Railway pre-deploy：``python -m app.cli migrate``）。

architecture_decisions §5。移植 ivy ``startup/migrations.py::_alembic_upgrade_lock`` 與
``apply_migration_lock_timeout``：session 級 advisory lock 串行化、等待有上限、逾時提示查
``pg_stat_activity``。去掉 ivy 的四態偵測、maintenance engine、blocking revision 守衛與 subprocess；
本專案在同一行程以 ``alembic.command.upgrade`` 執行。

- 只讀 ``MIGRATION_DATABASE_URL`` 與 ``DATABASE_URL``，不建立執行期 Settings。
- app_backend 的密碼在客戶端算成 SCRAM-SHA-256 verifier 後才送進 DB，明文不進 DB log。
- 錯誤訊息一律使用遮罩後的 URL。
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import psycopg
from alembic import command
from alembic.config import Config
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

# 與 BACKEND-017 advisory_key 同公式（md5 前 8 bytes → 63-bit 正整數）；本模組不 import locks.py
MIGRATION_LOCK_KEY: Final[int] = (
    int.from_bytes(
        hashlib.md5(b"migration|alembic_upgrade", usedforsecurity=False).digest()[:8], "big"
    )
    & 0x7FFF_FFFF_FFFF_FFFF
)
# 等待其他 migrate 釋放鎖的上限（程式常數，不是 env）
LOCK_WAIT_TIMEOUT_MS: Final = 600_000

BACKEND_ROLE: Final = "app_backend"
_SQLALCHEMY_SCHEME: Final = "postgresql+psycopg://"
_PLAIN_SCHEME: Final = "postgresql://"
_DEFAULT_PORT: Final = "5432"

_USERINFO_PASSWORD = re.compile(r"(://[^:/@?#]*:)[^@/?#]*@")
_QUERY_PASSWORD = re.compile(r"(?i)(password=)[^&#]*")


class MigrationConfigError(Exception):
    """env 缺漏或不合法；訊息不含密碼。"""


class MigrationLockTimeout(RuntimeError):
    """等待 migration advisory lock 逾時。"""


@dataclass(frozen=True)
class MigrationUrls:
    migration_url: str  # owner，postgresql+psycopg://
    backend_url: str  # app_backend，postgresql+psycopg://


def mask_url(url: str) -> str:
    """把 userinfo 與 query 中的密碼換成 ***。"""
    return _QUERY_PASSWORD.sub(r"\1***", _USERINFO_PASSWORD.sub(r"\1***@", url))


def _normalize(name: str, url: str) -> str:
    if url.startswith(_SQLALCHEMY_SCHEME):
        return url
    if url.startswith(_PLAIN_SCHEME):
        return _SQLALCHEMY_SCHEME + url.removeprefix(_PLAIN_SCHEME)
    raise MigrationConfigError(f"{name} 必須是 postgresql:// 或 postgresql+psycopg:// 開頭")


def _to_psycopg(url: str) -> str:
    return _PLAIN_SCHEME + url.removeprefix(_SQLALCHEMY_SCHEME)


def _parse(name: str, url: str) -> dict[str, Any]:
    try:
        return conninfo_to_dict(_to_psycopg(url))
    except psycopg.Error:
        # 解析器的錯誤訊息可能引用連線字串片段，不附原文
        raise MigrationConfigError(f"{name} 無法解析：{mask_url(url)}") from None


def _target(params: Mapping[str, Any]) -> tuple[str, str, str]:
    user = str(params.get("user") or "")
    return (
        str(params.get("host") or ""),
        str(params.get("port") or _DEFAULT_PORT),
        str(params.get("dbname") or user),  # libpq：未指定 dbname 時用使用者名稱
    )


def load_migration_urls(environ: Mapping[str, str]) -> MigrationUrls:
    raw: dict[str, str] = {}
    for name in ("MIGRATION_DATABASE_URL", "DATABASE_URL"):
        value = environ.get(name, "")
        if not value:
            raise MigrationConfigError(f"未設定 {name}")
        raw[name] = _normalize(name, value)
    migration_url = raw["MIGRATION_DATABASE_URL"]
    backend_url = raw["DATABASE_URL"]

    owner = _parse("MIGRATION_DATABASE_URL", migration_url)
    backend = _parse("DATABASE_URL", backend_url)
    masked = (
        f"（MIGRATION_DATABASE_URL={mask_url(migration_url)}，"
        f"DATABASE_URL={mask_url(backend_url)}）"
    )

    if backend.get("user") != BACKEND_ROLE:
        raise MigrationConfigError(f"DATABASE_URL 的使用者必須是 {BACKEND_ROLE}{masked}")
    if not backend.get("password"):
        raise MigrationConfigError(f"DATABASE_URL 必須帶 {BACKEND_ROLE} 的密碼{masked}")
    if owner.get("user") == backend.get("user"):
        raise MigrationConfigError(f"兩個 URL 的使用者不可相同{masked}")
    for label, owner_value, backend_value in zip(
        ("host", "port", "database"), _target(owner), _target(backend), strict=True
    ):
        if owner_value != backend_value:
            raise MigrationConfigError(f"兩個 URL 的 {label} 必須相同{masked}")
    return MigrationUrls(migration_url=migration_url, backend_url=backend_url)


def sync_backend_password(
    conn: psycopg.Connection[Any], password: str, *, role: str = BACKEND_ROLE
) -> None:
    """在客戶端算出 SCRAM-SHA-256 verifier 再 alter role；明文不送進 DB、不寫進 log。"""
    exists = conn.execute("select 1 from pg_roles where rolname = %s", (role,)).fetchone()
    if exists is None:
        raise RuntimeError(f"{role} 不存在")
    verifier = conn.pgconn.encrypt_password(
        password.encode("utf-8"), role.encode("utf-8"), b"scram-sha-256"
    ).decode("ascii")
    conn.execute(
        sql.SQL("alter role {} with login password {}").format(
            sql.Identifier(role), sql.Literal(verifier)
        )
    )


def _backend_password(urls: MigrationUrls) -> str:
    password = conninfo_to_dict(_to_psycopg(urls.backend_url)).get("password")
    if not password:
        raise MigrationConfigError(f"DATABASE_URL 必須帶 {BACKEND_ROLE} 的密碼")
    return str(password)


def _acquire_lock(conn: psycopg.Connection[Any], lock_wait_timeout_ms: int) -> None:
    # SET 不接受 bind 參數，改用 set_config（false = session 級）；
    # lock_timeout 對 pg_advisory_lock 的等待一樣生效
    conn.execute("select set_config('lock_timeout', %s, false)", (str(lock_wait_timeout_ms),))
    row = conn.execute("select pg_try_advisory_lock(%s)", (MIGRATION_LOCK_KEY,)).fetchone()
    if row is not None and row[0]:
        return
    print("另一個 migrate 正在執行，等待其完成", file=sys.stderr, flush=True)
    try:
        conn.execute("select pg_advisory_lock(%s)", (MIGRATION_LOCK_KEY,))
    except psycopg.errors.LockNotAvailable:
        raise MigrationLockTimeout(
            f"等待 migration advisory lock 逾時（{lock_wait_timeout_ms}ms）。"
            "持鎖的 migrate 可能已卡住，"
            "請以 pg_stat_activity / pg_locks 找出持鎖與擋路的連線並處理"
            "（必要時 pg_terminate_backend），再重新部署。"
        ) from None


def run_migrations(
    urls: MigrationUrls, *, alembic_ini: Path, lock_wait_timeout_ms: int = LOCK_WAIT_TIMEOUT_MS
) -> str:
    """持 advisory lock 執行 alembic upgrade head 並同步 app_backend 密碼，回傳套用後的 head。"""
    env_url = os.environ.get("MIGRATION_DATABASE_URL", "")
    if not env_url or _normalize("MIGRATION_DATABASE_URL", env_url) != urls.migration_url:
        # env.py 只讀環境變數；不一致時會在 A 庫持鎖、對 B 庫套 migration
        raise MigrationConfigError("環境變數 MIGRATION_DATABASE_URL 與 urls.migration_url 不一致")
    password = _backend_password(urls)

    with psycopg.connect(_to_psycopg(urls.migration_url), autocommit=True) as conn:
        _acquire_lock(conn, lock_wait_timeout_ms)
        try:
            command.upgrade(Config(str(alembic_ini)), "head")
            sync_backend_password(conn, password)
            versions = [r[0] for r in conn.execute("select version_num from alembic_version")]
        finally:
            # 連線已斷時鎖隨 session 結束自動釋放
            if not conn.closed:
                conn.execute("select pg_advisory_unlock(%s)", (MIGRATION_LOCK_KEY,))
    if len(versions) != 1:
        raise RuntimeError(f"alembic_version 應恰有一筆，實際為 {versions}")
    return str(versions[0])
