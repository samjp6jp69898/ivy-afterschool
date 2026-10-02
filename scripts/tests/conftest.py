"""scripts/tests 共用 fixture（INFRA-008）。

- repo_root：repo root 路徑（由本檔位置推得，不依賴 cwd）。
- run_cmd：執行外部指令並捕捉輸出，預設 cwd 為 repo root。
- fake_bin：在 tmp_path/bin 建立假的 supabase / uv / pnpm / docker / just 等指令並記錄呼叫。
- local_db_url / local_db_conn：只允許本機 loopback 的 DB 連線（integration 專用）。

使用 local_db_conn 的測試自動加 integration marker，預設 `just test` 不會執行。
"""

import json
import os
import stat
import subprocess
import sys
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

import psycopg
import pytest

pytest_plugins = ["pytester"]

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_DB_URL = "postgresql://postgres:postgres@127.0.0.1:54342/postgres"
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


class RunCmd(Protocol):
    def __call__(
        self,
        args: Sequence[str],
        *,
        cwd: Path | str | None = None,
        env: Mapping[str, str | None] | None = None,
        input: str | None = None,
        timeout: float = 120,
    ) -> subprocess.CompletedProcess[str]: ...


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return _REPO_ROOT


@pytest.fixture
def run_cmd() -> RunCmd:
    def _run(
        args: Sequence[str],
        *,
        cwd: Path | str | None = None,
        env: Mapping[str, str | None] | None = None,
        input: str | None = None,
        timeout: float = 120,
    ) -> subprocess.CompletedProcess[str]:
        merged = dict(os.environ)
        for key, value in (env or {}).items():
            if value is None:
                merged.pop(key, None)
            else:
                merged[key] = value
        return subprocess.run(  # noqa: S603  測試以固定參數呼叫指令
            list(args),
            cwd=cwd if cwd is not None else _REPO_ROOT,
            env=merged,
            input=input,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )

    return _run


_FAKE_SCRIPT = """#!{python}
import json, os, sys
with open({log!r}, "a", encoding="utf-8") as f:
    f.write(json.dumps({{"argv": sys.argv[1:], "cwd": os.getcwd()}}, ensure_ascii=False) + "\\n")
sys.stdout.write({stdout!r})
sys.exit({exit_code})
"""


class FakeBin:
    """在 <tmp_path>/bin 建立假指令。

    每次被呼叫時把 argv 與 cwd 以 JSON 一行附加到 <name>.calls.jsonl。
    """

    def __init__(self, bin_dir: Path) -> None:
        self.bin_dir = bin_dir
        self.bin_dir.mkdir(parents=True, exist_ok=True)

    def add(self, name: str, stdout: str = "", exit_code: int = 0) -> Path:
        path = self.bin_dir / name
        path.write_text(
            _FAKE_SCRIPT.format(
                python=sys.executable,
                log=str(self._log_path(name)),
                stdout=stdout,
                exit_code=exit_code,
            ),
            encoding="utf-8",
        )
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        return path

    def calls(self, name: str) -> list[dict[str, Any]]:
        log = self._log_path(name)
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]

    def path_env(self) -> str:
        return f"{self.bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"

    def _log_path(self, name: str) -> Path:
        return self.bin_dir / f"{name}.calls.jsonl"


@pytest.fixture
def fake_bin(tmp_path: Path) -> FakeBin:
    return FakeBin(tmp_path / "bin")


@pytest.fixture
def local_db_url() -> str:
    url = os.environ.get("SCRIPTS_TEST_DATABASE_URL", _DEFAULT_DB_URL)
    host = urlsplit(url).hostname
    if host not in _LOOPBACK_HOSTS:
        pytest.fail(f"只允許本機 loopback DB：{host}")
    return url


@pytest.fixture
def local_db_conn(local_db_url: str) -> Iterator[psycopg.Connection[Any]]:
    try:
        conn = psycopg.connect(local_db_url, autocommit=False, connect_timeout=3)
    except psycopg.OperationalError as exc:
        pytest.fail(f"無法連線本機 Supabase（{exc.__class__.__name__}），先跑 just db-start")
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


# tryfirst：必須在 -m 依 marker 篩選（deselect）之前補上 integration marker
@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if "local_db_conn" in getattr(item, "fixturenames", ()):
            item.add_marker(pytest.mark.integration)
