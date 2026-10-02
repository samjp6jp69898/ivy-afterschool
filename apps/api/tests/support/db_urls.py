"""測試用 DB 連線 URL 解析與 loopback 守衛（INFRA-041）。

供 SQLAlchemy session fixture（INFRA-010）與 psycopg fixture（DB-002）共用。全部是純函式：
不連線、不依賴任何 DB 物件（`conninfo_to_dict` 只解析字串）。

loopback 守衛是唯一防線：psycopg 走 libpq 的 C socket，pytest-socket 攔不到。libpq 實際連線的
位址可被 URL query（host / hostaddr / service）與環境變數（PGHOST / PGHOSTADDR / PGSERVICE，
只補 URL 未指定的參數）覆寫，因此依 libpq 的規則判斷；連線後再以 `assert_connected_loopback`
用 `conn.info.hostaddr` 複驗。SQLAlchemy 會把 URL query 原樣交給 psycopg，規則與直連相同。
"""

import os
from collections.abc import Mapping

import psycopg
from psycopg.conninfo import conninfo_to_dict

DEFAULT_BACKEND_URL = "postgresql+psycopg://app_backend:app_backend_local@127.0.0.1:54342/postgres"
DEFAULT_OWNER_URL = "postgresql+psycopg://postgres:postgres@127.0.0.1:54342/postgres"

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
LOOPBACK_ADDRS = frozenset({"127.0.0.1", "::1"})

_SQLALCHEMY_SCHEME = "postgresql+psycopg://"
_PSYCOPG_SCHEME = "postgresql://"
_ERROR_PREFIX = "只允許本機 loopback DB："


def to_psycopg_dsn(url: str) -> str:
    """把 SQLAlchemy 的 `postgresql+psycopg://` 轉成 psycopg 可直接使用的 `postgresql://`。"""
    if url.startswith(_SQLALCHEMY_SCHEME):
        return _PSYCOPG_SCHEME + url.removeprefix(_SQLALCHEMY_SCHEME)
    return url


def to_sqlalchemy_url(url: str) -> str:
    """把 `postgresql://` 轉成 SQLAlchemy 的 `postgresql+psycopg://`；其他形式原樣回傳。"""
    if url.startswith(_PSYCOPG_SCHEME):
        return _SQLALCHEMY_SCHEME + url.removeprefix(_PSYCOPG_SCHEME)
    return url


def loopback_violation(url: str, environ: Mapping[str, str]) -> str | None:
    """依 libpq 規則找出會讓連線離開本機的設定；全部是 loopback 時回傳 None。

    host / hostaddr 先看 URL、URL 未指定時再看 PGHOST / PGHOSTADDR；逗號清單逐項檢查。
    service（URL 或 PGSERVICE）可指定任意位址，一律拒絕；host 與 PGHOST 都未指定時 libpq
    會改用 unix socket 預設值，同樣拒絕。
    """
    try:
        params = conninfo_to_dict(to_psycopg_dsn(url))
    except psycopg.ProgrammingError as exc:
        return f"無法解析連線字串（{exc}）"
    if "service" in params:
        return f"service={params['service']}（service 檔可指定任意位址，不允許）"
    if environ.get("PGSERVICE"):
        return f"環境變數 PGSERVICE={environ['PGSERVICE']}（service 檔可指定任意位址，不允許）"

    checks = (("host", "PGHOST", LOOPBACK_HOSTS), ("hostaddr", "PGHOSTADDR", LOOPBACK_ADDRS))
    for key, env_name, allowed in checks:
        raw = params.get(key)
        value = None if raw is None else str(raw)
        source = key
        if value is None and environ.get(env_name):
            value = environ[env_name]
            source = f"環境變數 {env_name}"
        if value is None:
            if key == "host":
                return "未指定 host（libpq 會改用 unix socket 預設值），請明確寫 127.0.0.1"
            continue
        for part in value.split(","):
            # hostaddr 的空項目代表該主機不指定位址；host 的空項目代表走 unix socket
            if key == "hostaddr" and part == "":
                continue
            if part not in allowed:
                return f"{source}={part}"
    return None


def assert_loopback(url: str, environ: Mapping[str, str] | None = None) -> None:
    """連線目標不是本機 loopback 時 raise ValueError。`environ` 預設為 os.environ。"""
    violation = loopback_violation(url, os.environ if environ is None else environ)
    if violation is not None:
        raise ValueError(_ERROR_PREFIX + violation)


def assert_connected_loopback(hostaddr: str) -> None:
    """連線後的縱深防禦：實際位址（`conn.info.hostaddr`，unix socket 為空字串）必須是 loopback。"""
    if hostaddr not in LOOPBACK_ADDRS:
        raise ValueError(f"{_ERROR_PREFIX}實際連線位址 {hostaddr or '（unix socket）'}")


def _url_from_env(env_name: str, default: str) -> str:
    url = to_sqlalchemy_url(os.environ.get(env_name, default))
    assert_loopback(url)
    return url


def backend_url() -> str:
    """後端專用角色 app_backend 的測試連線 URL（`TEST_DB_BACKEND_URL`）。"""
    return _url_from_env("TEST_DB_BACKEND_URL", DEFAULT_BACKEND_URL)


def owner_url() -> str:
    """owner 角色的測試連線 URL（`TEST_DB_OWNER_URL`）。

    只供明確的 owner 用途（建立探針物件、清表、角色切換驗證），不得作為 backend 連線失敗時的替代。
    """
    return _url_from_env("TEST_DB_OWNER_URL", DEFAULT_OWNER_URL)
