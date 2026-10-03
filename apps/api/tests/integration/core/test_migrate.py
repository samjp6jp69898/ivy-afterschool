"""BACKEND-535：python -m app.cli migrate。

advisory lock 串行化的 alembic upgrade 與 app_backend 密碼同步。


- 需要 DB 的案例以 owner 建立暫存 database `migrate_probe_<uuid8>`，測試結束 drop（with force）。
- 暫存角色同樣以 `migrate_probe_` 為前綴，建在交易內、測試結束 rollback。
- 不修改共用本機 DB 的 app_backend 密碼：端到端案例使用與本機相同的 'app_backend_local'。
- 連線一律經 tests.support.db_urls 的 owner_url() 與 loopback 守衛。
"""

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from psycopg import sql

from app import cli
from app.core import migrate
from app.core.migrate import (
    MIGRATION_LOCK_KEY,
    MigrationConfigError,
    MigrationLockTimeout,
    MigrationUrls,
    load_migration_urls,
    run_migrations,
    sync_backend_password,
)
from tests.support import db_urls

API_DIR = Path(__file__).resolve().parents[3]
ALEMBIC_INI = API_DIR / "alembic.ini"
LOCAL_BACKEND_PW = "app_backend_local"  # 與本機 just db-reset 設定的值相同

Conn = psycopg.Connection[tuple[Any, ...]]

_OWNER_URL = "postgresql://postgres:pw@127.0.0.1:54342/postgres"
_BACKEND_URL = "postgresql://app_backend:secret1@127.0.0.1:54342/postgres"


def _owner_connect(dbname: str = "postgres", *, autocommit: bool = True) -> Conn:
    dsn = db_urls.to_psycopg_dsn(db_urls.owner_url())
    conn = psycopg.connect(dsn, dbname=dbname, autocommit=autocommit)
    db_urls.assert_connected_loopback(conn.info.hostaddr)
    return conn


def _with_dbname(url: str, dbname: str) -> str:
    base, _, _ = url.rpartition("/")
    return f"{base}/{dbname}"


@pytest.fixture
def probe_db() -> Iterator[str]:
    name = f"migrate_probe_{uuid4().hex[:8]}"
    with _owner_connect() as conn:
        conn.execute(sql.SQL("create database {}").format(sql.Identifier(name)))
    try:
        yield name
    finally:
        with _owner_connect() as conn:
            conn.execute(
                sql.SQL("drop database if exists {} with (force)").format(sql.Identifier(name))
            )


@pytest.fixture
def probe_urls(probe_db: str, monkeypatch: pytest.MonkeyPatch) -> MigrationUrls:
    owner = _with_dbname(db_urls.to_sqlalchemy_url(db_urls.owner_url()), probe_db)
    backend_base = db_urls.to_sqlalchemy_url(db_urls.backend_url())
    backend = _with_dbname(backend_base, probe_db)
    assert f":{LOCAL_BACKEND_PW}@" in backend
    monkeypatch.setenv("MIGRATION_DATABASE_URL", owner)
    return MigrationUrls(migration_url=owner, backend_url=backend)


def _fetch_one(dbname: str, query: str) -> Any:
    with _owner_connect(dbname) as conn:
        row = conn.execute(query).fetchone()
    assert row is not None
    return row[0]


def _alembic_head() -> str:
    heads = ScriptDirectory.from_config(Config(str(ALEMBIC_INI))).get_heads()
    assert len(heads) == 1
    return heads[0]


# --- load_migration_urls ------------------------------------------------------------------


def test_migrate_load_urls_valid() -> None:
    urls = load_migration_urls({"MIGRATION_DATABASE_URL": _OWNER_URL, "DATABASE_URL": _BACKEND_URL})

    assert urls.migration_url == "postgresql+psycopg://postgres:pw@127.0.0.1:54342/postgres"
    assert urls.backend_url == "postgresql+psycopg://app_backend:secret1@127.0.0.1:54342/postgres"


def test_migrate_load_urls_accepts_sqlalchemy_scheme_and_default_port() -> None:
    urls = load_migration_urls(
        {
            "MIGRATION_DATABASE_URL": "postgresql+psycopg://postgres:pw@db.internal/railway",
            "DATABASE_URL": "postgresql://app_backend:secret1@db.internal:5432/railway",
        }
    )

    assert urls.migration_url.startswith("postgresql+psycopg://")
    assert urls.backend_url.startswith("postgresql+psycopg://")


@pytest.mark.parametrize(
    ("environ", "fragment"),
    [
        ({"DATABASE_URL": _BACKEND_URL}, "MIGRATION_DATABASE_URL"),
        ({"MIGRATION_DATABASE_URL": _OWNER_URL}, "DATABASE_URL"),
        ({"MIGRATION_DATABASE_URL": "", "DATABASE_URL": _BACKEND_URL}, "MIGRATION_DATABASE_URL"),
        (
            {
                "MIGRATION_DATABASE_URL": _OWNER_URL,
                "DATABASE_URL": "postgresql://postgres:secret1@127.0.0.1:54342/postgres",
            },
            "app_backend",
        ),
        (
            {
                "MIGRATION_DATABASE_URL": _OWNER_URL,
                "DATABASE_URL": "postgresql://app_backend@127.0.0.1:54342/postgres",
            },
            "密碼",
        ),
        (
            {
                "MIGRATION_DATABASE_URL": _OWNER_URL,
                "DATABASE_URL": "postgresql://app_backend:@127.0.0.1:54342/postgres",
            },
            "密碼",
        ),
        (
            {
                "MIGRATION_DATABASE_URL": _OWNER_URL,
                "DATABASE_URL": "postgresql://app_backend:secret1@127.0.0.1:54342/other",
            },
            "database",
        ),
        (
            {
                "MIGRATION_DATABASE_URL": _OWNER_URL,
                "DATABASE_URL": "postgresql://app_backend:secret1@10.0.0.9:54342/postgres",
            },
            "host",
        ),
        (
            {
                "MIGRATION_DATABASE_URL": _OWNER_URL,
                "DATABASE_URL": "postgresql://app_backend:secret1@127.0.0.1:5432/postgres",
            },
            "port",
        ),
        (
            {
                "MIGRATION_DATABASE_URL": _OWNER_URL,
                "DATABASE_URL": "postgresql://app_backend:secret1@127.0.0.1:54342/postgres?host=10.0.0.9",
            },
            "host",
        ),
        (
            {
                "MIGRATION_DATABASE_URL": "postgresql://app_backend:pw@127.0.0.1:54342/postgres",
                "DATABASE_URL": _BACKEND_URL,
            },
            "相同",
        ),
        (
            {
                "MIGRATION_DATABASE_URL": "mysql://postgres:pw@127.0.0.1/x",
                "DATABASE_URL": _BACKEND_URL,
            },
            "postgresql",
        ),
        (
            {
                "MIGRATION_DATABASE_URL": _OWNER_URL,
                "DATABASE_URL": "postgresql://app_backend:secret1@[bad",
            },
            "DATABASE_URL",
        ),
        # review-r3-infra 打回 1：hostaddr / service 也決定實際連線位址
        (
            {
                "MIGRATION_DATABASE_URL": "postgresql://postgres:pw@db.internal:5432/railway?hostaddr=10.0.0.1",
                "DATABASE_URL": "postgresql://app_backend:secret1@db.internal:5432/railway?hostaddr=10.0.0.2",
            },
            "hostaddr",
        ),
        (
            {
                "MIGRATION_DATABASE_URL": "postgresql://postgres:pw@db.internal:5432/railway",
                "DATABASE_URL": "postgresql://app_backend:secret1@db.internal:5432/railway?hostaddr=10.9.9.9",
            },
            "hostaddr",
        ),
        (
            {
                "MIGRATION_DATABASE_URL": "postgresql://postgres:pw@/railway?service=prod",
                "DATABASE_URL": "postgresql://app_backend:secret1@/railway?service=staging",
            },
            "service",
        ),
        (
            {
                "MIGRATION_DATABASE_URL": "postgresql://postgres:pw@db.internal:5432/railway?service=x",
                "DATABASE_URL": "postgresql://app_backend:secret1@db.internal:5432/railway",
            },
            "service",
        ),
        (
            {
                "MIGRATION_DATABASE_URL": _OWNER_URL,
                "DATABASE_URL": _BACKEND_URL,
                "PGSERVICE": "prod",
            },
            "PGSERVICE",
        ),
    ],
)
def test_migrate_load_urls_rejects(environ: dict[str, str], fragment: str) -> None:
    with pytest.raises(MigrationConfigError) as exc_info:
        load_migration_urls(environ)

    message = str(exc_info.value)
    assert fragment in message
    assert "secret1" not in message
    assert "pw@" not in message


def test_migrate_load_urls_password_in_query_is_masked() -> None:
    with pytest.raises(MigrationConfigError) as exc_info:
        load_migration_urls(
            {
                "MIGRATION_DATABASE_URL": "postgresql://postgres@127.0.0.1:54342/postgres?password=pw",
                "DATABASE_URL": "postgresql://app_backend@127.0.0.1:54342/postgres?password=secret1&host=10.0.0.9",
            }
        )

    assert "secret1" not in str(exc_info.value)
    assert "password=pw" not in str(exc_info.value)


_RESERVED_PASSWORDS = ["Ab3/xYz+Q9=", "pa?ss", "pa#ss", "pa@ss", "a[b]c"]


def _leaks(text: str, password: str) -> bool:
    """完整密碼或其 4 字元以上的片段（以保留字元切開後）出現在輸出中。"""
    pieces = [password] + [p for p in re.split(r"[/?#@\[\]]", password) if len(p) >= 4]
    return any(piece in text for piece in pieces)


@pytest.mark.parametrize("password", _RESERVED_PASSWORDS)
@pytest.mark.parametrize("which", ["DATABASE_URL", "MIGRATION_DATABASE_URL"])
def test_migrate_load_urls_reserved_chars_in_password(password: str, which: str) -> None:
    environ = {"MIGRATION_DATABASE_URL": _OWNER_URL, "DATABASE_URL": _BACKEND_URL}
    user = "app_backend" if which == "DATABASE_URL" else "postgres"
    environ[which] = f"postgresql://{user}:{password}@db.internal:5432/railway"

    with pytest.raises(MigrationConfigError) as exc_info:
        load_migration_urls(environ)

    message = str(exc_info.value)
    assert which in message
    assert "%2F" in message  # 提示以百分比編碼
    assert not _leaks(message, password)
    assert "secret1" not in message
    assert "pw@" not in message


def test_migrate_load_urls_accepts_percent_encoded_password() -> None:
    urls = load_migration_urls(
        {
            "MIGRATION_DATABASE_URL": "postgresql://postgres:pw@db.internal:5432/railway",
            "DATABASE_URL": "postgresql://app_backend:Ab3%2FxYz%2BQ9%3D@db.internal:5432/railway",
        }
    )

    assert urls.backend_url.endswith("@db.internal:5432/railway")


@pytest.mark.parametrize(
    ("backend", "fragment"),
    [
        ("postgresql://app_backend:{pw}@other.internal:5432/railway", "host"),
        ("postgresql://app_backend:{pw}@db.internal:6543/railway", "port"),
        ("postgresql://app_backend:{pw}@db.internal:5432/other", "database"),
        ("postgresql://app_backend:{pw}@db.internal:5432/railway?hostaddr=10.0.0.9", "hostaddr"),
        ("postgresql://someone:{pw}@db.internal:5432/railway", "app_backend"),
    ],
)
def test_migrate_load_urls_errors_never_echo_password(backend: str, fragment: str) -> None:
    encoded = "Ab3%2FxY%3Fz%23Q%409%3D"  # 解碼後為 Ab3/xY?z#Q@9=
    decoded = "Ab3/xY?z#Q@9="

    with pytest.raises(MigrationConfigError) as exc_info:
        load_migration_urls(
            {
                "MIGRATION_DATABASE_URL": f"postgresql://postgres:{encoded}@db.internal:5432/railway",
                "DATABASE_URL": backend.format(pw=encoded),
            }
        )

    message = str(exc_info.value)
    assert fragment in message
    assert decoded not in message
    assert encoded not in message
    assert "Ab3" not in message


@pytest.mark.parametrize(
    "backend_url",
    [
        "postgresql://app_backend:Ab3/xYz+Q9=@db.internal:5432/railway",
        "postgresql://app_backend:pa@ss@db.internal:5432/railway",
        "postgresql://app_backend:pa?ss@db.internal:5432/railway",
    ],
)
def test_cli_migrate_reserved_password_not_printed(
    backend_url: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(
        "MIGRATION_DATABASE_URL", "postgresql://postgres:pw@db.internal:5432/railway"
    )
    monkeypatch.setenv("DATABASE_URL", backend_url)
    password = backend_url.split("app_backend:", 1)[1].rsplit("@", 1)[0]

    def must_not_connect(*args: Any, **kwargs: Any) -> str:
        # 設定檢查必須在連線前擋下；不可真的去解析 db.internal（libpq 的 DNS 查詢攔不住）
        raise AssertionError("不應執行到 run_migrations")

    monkeypatch.setattr(cli, "run_migrations", must_not_connect)

    assert cli.main(["migrate"]) == 2

    out = capsys.readouterr()
    assert not _leaks(out.out + out.err, password)
    assert "DATABASE_URL" in out.err


def test_migrate_lock_key_formula() -> None:
    import hashlib

    digest = hashlib.md5(b"migration|alembic_upgrade", usedforsecurity=False).digest()
    assert int.from_bytes(digest[:8], "big") & 0x7FFF_FFFF_FFFF_FFFF == MIGRATION_LOCK_KEY
    assert 0 < MIGRATION_LOCK_KEY < 2**63
    assert migrate.LOCK_WAIT_TIMEOUT_MS == 600_000


# --- sync_backend_password ----------------------------------------------------------------


@pytest.fixture
def owner_tx_conn() -> Iterator[Conn]:
    """交易內的 owner 連線：測試建立的角色在結束時 rollback。"""
    conn = _owner_connect(autocommit=False)
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


def test_migrate_sync_backend_password_sets_scram_verifier(
    owner_tx_conn: Conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    role = f"migrate_probe_{uuid4().hex[:8]}"
    owner_tx_conn.execute(sql.SQL("create role {} nologin").format(sql.Identifier(role)))
    sent: list[str] = []
    original_execute = owner_tx_conn.execute

    def recording_execute(query: Any, params: Any = None, **kwargs: Any) -> Any:
        text = query.as_string(owner_tx_conn) if isinstance(query, sql.Composable) else str(query)
        sent.append(text + " " + repr(params))
        return original_execute(query, params, **kwargs)

    monkeypatch.setattr(owner_tx_conn, "execute", recording_execute)

    sync_backend_password(owner_tx_conn, "probe-pass-1", role=role)

    row = original_execute(
        "select rolcanlogin, rolpassword from pg_authid where rolname = %s", (role,)
    ).fetchone()
    assert row is not None
    can_login, verifier = row
    assert can_login is True
    assert verifier.startswith("SCRAM-SHA-256$")
    assert sent, "應有送出 SQL"
    assert all("probe-pass-1" not in text for text in sent)


def test_migrate_sync_backend_password_missing_role(owner_tx_conn: Conn) -> None:
    with pytest.raises(RuntimeError, match="no_such_role_x"):
        sync_backend_password(owner_tx_conn, "probe-pass-1", role="no_such_role_x")


def test_migrate_sync_backend_password_quotes_role(owner_tx_conn: Conn) -> None:
    # 角色名稱以 Identifier 組 SQL：含大寫與引號的名稱也只作用在該角色
    role = f'migrate_probe_{uuid4().hex[:8]}"X'
    owner_tx_conn.execute(sql.SQL("create role {} nologin").format(sql.Identifier(role)))

    sync_backend_password(owner_tx_conn, "probe-pass-2", role=role)

    row = owner_tx_conn.execute(
        "select rolcanlogin from pg_roles where rolname = %s", (role,)
    ).fetchone()
    assert row == (True,)


# --- run_migrations -----------------------------------------------------------------------


def test_migrate_run_upgrades_probe_database(probe_db: str, probe_urls: MigrationUrls) -> None:
    head = run_migrations(probe_urls, alembic_ini=ALEMBIC_INI)

    assert head == _alembic_head()
    assert _fetch_one(probe_db, "select version_num from alembic_version") == head
    # app_backend 以本機密碼登入暫存 database（密碼同步後仍有效、可登入）
    with psycopg.connect(db_urls.to_psycopg_dsn(probe_urls.backend_url)) as backend:
        db_urls.assert_connected_loopback(backend.info.hostaddr)
        assert backend.execute("select current_user").fetchone() == ("app_backend",)


def test_migrate_run_is_idempotent(probe_db: str, probe_urls: MigrationUrls) -> None:
    first = run_migrations(probe_urls, alembic_ini=ALEMBIC_INI)
    second = run_migrations(probe_urls, alembic_ini=ALEMBIC_INI)

    assert first == second == _alembic_head()


def test_migrate_lock_timeout(probe_db: str, probe_urls: MigrationUrls) -> None:
    with _owner_connect(probe_db) as holder:
        assert holder.execute("select pg_advisory_lock(%s)", (MIGRATION_LOCK_KEY,)).fetchone()

        with pytest.raises(MigrationLockTimeout) as exc_info:
            run_migrations(probe_urls, alembic_ini=ALEMBIC_INI, lock_wait_timeout_ms=500)

    assert "pg_stat_activity" in str(exc_info.value)
    assert _fetch_one(probe_db, "select to_regclass('public.alembic_version') is null") is True


def test_migrate_releases_lock_on_failure(
    probe_db: str, probe_urls: MigrationUrls, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(command, "upgrade", boom)

    with pytest.raises(RuntimeError, match="boom"):
        run_migrations(probe_urls, alembic_ini=ALEMBIC_INI)

    with _owner_connect(probe_db) as other:
        assert other.execute(
            "select pg_try_advisory_lock(%s)", (MIGRATION_LOCK_KEY,)
        ).fetchone() == (True,)


def test_migrate_run_rejects_env_mismatch(
    probe_db: str, probe_urls: MigrationUrls, monkeypatch: pytest.MonkeyPatch
) -> None:
    # env.py 只讀 MIGRATION_DATABASE_URL；與參數不一致時會在 A 庫持鎖、對 B 庫套 migration
    monkeypatch.setenv("MIGRATION_DATABASE_URL", db_urls.to_sqlalchemy_url(db_urls.owner_url()))

    with pytest.raises(MigrationConfigError, match="MIGRATION_DATABASE_URL"):
        run_migrations(probe_urls, alembic_ini=ALEMBIC_INI)

    assert _fetch_one(probe_db, "select to_regclass('public.alembic_version') is null") is True


# --- cli ----------------------------------------------------------------------------------


@pytest.fixture
def cli_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MIGRATION_DATABASE_URL", _OWNER_URL)
    monkeypatch.setenv("DATABASE_URL", _BACKEND_URL)


@pytest.mark.usefixtures("cli_env")
def test_cli_migrate_exit_codes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[tuple[MigrationUrls, Path]] = []

    def ok(urls: MigrationUrls, *, alembic_ini: Path, **kwargs: Any) -> str:
        calls.append((urls, alembic_ini))
        return "db040"

    monkeypatch.setattr(cli, "run_migrations", ok)
    assert cli.main(["migrate"]) == 0
    out = capsys.readouterr()
    assert "db040" in out.out
    assert calls[0][1] == ALEMBIC_INI
    assert calls[0][0].backend_url.endswith("@127.0.0.1:54342/postgres")
    assert "secret1" not in out.out + out.err

    def bad_config(environ: Any) -> MigrationUrls:
        raise MigrationConfigError("DATABASE_URL 的使用者必須是 app_backend")

    monkeypatch.setattr(cli, "load_migration_urls", bad_config)
    assert cli.main(["migrate"]) == 2
    out = capsys.readouterr()
    assert "app_backend" in out.err
    assert "secret1" not in out.out + out.err


@pytest.mark.usefixtures("cli_env")
def test_cli_migrate_lock_timeout_and_other_errors(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def timeout(urls: MigrationUrls, **kwargs: Any) -> str:
        raise MigrationLockTimeout("等待 migration advisory lock 逾時，請查 pg_stat_activity")

    monkeypatch.setattr(cli, "run_migrations", timeout)
    assert cli.main(["migrate"]) == 1
    out = capsys.readouterr()
    assert "MigrationLockTimeout" in out.err
    assert "pg_stat_activity" in out.err
    assert "secret1" not in out.out + out.err

    def leaky(urls: MigrationUrls, **kwargs: Any) -> str:
        # 例外訊息意外帶出連線字串時，CLI 仍不得印出密碼
        raise RuntimeError(f"connect failed: {_BACKEND_URL} / {_OWNER_URL}")

    monkeypatch.setattr(cli, "run_migrations", leaky)
    assert cli.main(["migrate"]) == 1
    out = capsys.readouterr()
    assert "RuntimeError" in out.err
    assert "secret1" not in out.out + out.err
    assert "pw@" not in out.out + out.err


def test_cli_requires_subcommand(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli.main([])

    assert exc_info.value.code == 2
