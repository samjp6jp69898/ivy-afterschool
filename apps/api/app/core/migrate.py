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

# 帳號密碼段內必須百分比編碼的保留字元：libpq 遇到未編碼的 / 會把密碼片段解析成 port / dbname、
# @ 會解析成 host，SQLAlchemy 的解析結果又不同；只能在解析前就拒絕
_USERINFO_RESERVED: Final = frozenset("/?#@[]")
_FRAGMENT_SPLIT = re.compile(r"[/?#@\[\]]")
_MIN_FRAGMENT: Final = 4
_URL_VARS: Final = ("MIGRATION_DATABASE_URL", "DATABASE_URL")


class MigrationConfigError(Exception):
    """env 缺漏或不合法；訊息不含密碼。"""


class MigrationLockTimeout(RuntimeError):
    """等待 migration advisory lock 逾時。"""


@dataclass(frozen=True)
class MigrationUrls:
    migration_url: str  # owner，postgresql+psycopg://
    backend_url: str  # app_backend，postgresql+psycopg://


def _userinfo(url: str) -> str:
    """``://`` 之後到最後一個 ``@`` 之前的原始帳號密碼段；沒有 ``@`` 時為空字串。"""
    rest = url.split("://", 1)[-1]
    at = rest.rfind("@")
    return rest[:at] if at >= 0 else ""


def secret_fragments(environ: Mapping[str, str]) -> list[str]:
    """兩個 URL 可能出現在輸出中的機密字串，供錯誤訊息與 CLI 輸出遮罩。

    - 整串：URL 原樣與兩種 scheme 的寫法、``user:password`` 段——夠長，不論密碼多短都能整段遮掉。
    - 密碼（原始與 libpq 解析後）及其以保留字元切開的片段：只取 4 字元以上，
      太短的片段一換就會破壞訊息；訊息本身已不回顯 URL，這裡只是最後一道。
    """
    found: set[str] = set()
    for name in _URL_VARS:
        url = environ.get(name, "")
        if not url:
            continue
        rest = url.split("://", 1)[-1]
        userinfo = _userinfo(url)
        _, has_password, raw_password = userinfo.partition(":")
        found.update({url, _SQLALCHEMY_SCHEME + rest, _PLAIN_SCHEME + rest})
        passwords = []
        if has_password:
            found.add(userinfo)
            passwords.append(raw_password)
        try:
            parsed = conninfo_to_dict(_to_psycopg(_normalize(name, url)))
        except (MigrationConfigError, psycopg.Error):
            parsed = {}
        if parsed.get("password"):
            passwords.append(str(parsed["password"]))
        for password in passwords:
            pieces = [password, *_FRAGMENT_SPLIT.split(password)]
            found.update(piece for piece in pieces if len(piece) >= _MIN_FRAGMENT)
    return sorted(found, key=len, reverse=True)


def scrub(text: str, fragments: list[str]) -> str:
    """把密碼片段換成 ***（長的先換，避免短片段先替換後長片段比對不到）。"""
    for fragment in sorted(fragments, key=len, reverse=True):
        text = text.replace(fragment, "***")
    return text


def _normalize(name: str, url: str) -> str:
    if url.startswith(_SQLALCHEMY_SCHEME):
        return url
    if url.startswith(_PLAIN_SCHEME):
        return _SQLALCHEMY_SCHEME + url.removeprefix(_PLAIN_SCHEME)
    raise MigrationConfigError(f"{name} 必須是 postgresql:// 或 postgresql+psycopg:// 開頭")


def _to_psycopg(url: str) -> str:
    return _PLAIN_SCHEME + url.removeprefix(_SQLALCHEMY_SCHEME)


def _parse(name: str, url: str) -> dict[str, Any]:
    if any(ch in _USERINFO_RESERVED for ch in _userinfo(url)):
        raise MigrationConfigError(
            f"{name} 的帳號或密碼含未編碼的保留字元（/ ? # @ [ ]），"
            "請以百分比編碼後再設定（例如 / → %2F、@ → %40）"
        )
    try:
        params = conninfo_to_dict(_to_psycopg(url))
    except psycopg.Error:
        # 解析器的錯誤訊息可能引用連線字串片段，不附原文
        raise MigrationConfigError(
            f"{name} 無法解析（格式應為 postgresql://user:password@host:port/dbname）"
        ) from None
    if "service" in params:
        raise MigrationConfigError(f"{name} 不可使用 service（service 檔可指定任意位址）")
    return params


def _target(params: Mapping[str, Any]) -> tuple[str, str, str, str]:
    user = str(params.get("user") or "")
    return (
        str(params.get("host") or ""),
        str(params.get("hostaddr") or ""),
        str(params.get("port") or _DEFAULT_PORT),
        str(params.get("dbname") or user),  # libpq：未指定 dbname 時用使用者名稱
    )


def _describe(params: Mapping[str, Any]) -> str:
    """解析後欄位組成的連線描述，不含密碼。"""
    host, hostaddr, port, dbname = _target(params)
    addr = f"(hostaddr={hostaddr})" if hostaddr else ""
    return f"{params.get('user') or '-'}@{host or '-'}{addr}:{port}/{dbname}"


def _load(environ: Mapping[str, str]) -> MigrationUrls:
    if environ.get("PGSERVICE"):
        raise MigrationConfigError("環境變數 PGSERVICE 不可設定（service 檔可指定任意位址）")
    raw: dict[str, str] = {}
    for name in _URL_VARS:
        value = environ.get(name, "")
        if not value:
            raise MigrationConfigError(f"未設定 {name}")
        raw[name] = _normalize(name, value)
    migration_url = raw["MIGRATION_DATABASE_URL"]
    backend_url = raw["DATABASE_URL"]

    owner = _parse("MIGRATION_DATABASE_URL", migration_url)
    backend = _parse("DATABASE_URL", backend_url)
    described = f"（MIGRATION_DATABASE_URL={_describe(owner)}，DATABASE_URL={_describe(backend)}）"

    if backend.get("user") != BACKEND_ROLE:
        raise MigrationConfigError(f"DATABASE_URL 的使用者必須是 {BACKEND_ROLE}{described}")
    if not backend.get("password"):
        raise MigrationConfigError(f"DATABASE_URL 必須帶 {BACKEND_ROLE} 的密碼{described}")
    if owner.get("user") == backend.get("user"):
        raise MigrationConfigError(f"兩個 URL 的使用者不可相同{described}")
    for label, owner_value, backend_value in zip(
        ("host", "hostaddr", "port", "database"), _target(owner), _target(backend), strict=True
    ):
        if owner_value != backend_value:
            raise MigrationConfigError(f"兩個 URL 的 {label} 必須相同{described}")
    return MigrationUrls(migration_url=migration_url, backend_url=backend_url)


def load_migration_urls(environ: Mapping[str, str]) -> MigrationUrls:
    """只讀 MIGRATION_DATABASE_URL 與 DATABASE_URL；錯誤訊息不含密碼。"""
    message: str
    try:
        return _load(environ)
    except MigrationConfigError as exc:
        # 最後一道遮罩；在 except 之外 raise，原例外不掛在 __context__
        message = scrub(str(exc), secret_fragments(environ))
    raise MigrationConfigError(message)


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
