"""INFRA-021：justfile 的參數守衛與路徑路由回歸測試（鎖住 INFRA-002「禁止全量執行」的行為）。

一律以 `just --justfile <repo>/justfile` 執行，PATH 前置 fake_bin（假的 supabase / uv / pnpm /
docker），守衛失效時也只會呼叫到假指令，不會真的啟動任何服務。
"""

from __future__ import annotations

import socket
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import pytest

if TYPE_CHECKING:
    from conftest import FakeBin, RunCmd

_FAKE_TOOLS = ("supabase", "uv", "pnpm", "docker")


class Just(Protocol):
    def __call__(
        self, *args: str, env: Mapping[str, str | None] | None = None
    ) -> subprocess.CompletedProcess[str]: ...


@pytest.fixture
def just(run_cmd: RunCmd, fake_bin: FakeBin, repo_root: Path) -> Just:
    for tool in _FAKE_TOOLS:
        fake_bin.add(tool)

    def _just(
        *args: str, env: Mapping[str, str | None] | None = None
    ) -> subprocess.CompletedProcess[str]:
        merged: dict[str, str | None] = {"PATH": fake_bin.path_env()}
        merged.update(env or {})
        # stdin 一律給空字串（非 TTY），避免互動式 recipe 等待輸入
        return run_cmd(
            ["just", "--justfile", str(repo_root / "justfile"), *args], env=merged, input=""
        )

    return _just


def _fake_calls(fake_bin: FakeBin) -> dict[str, list[list[str]]]:
    return {tool: [c["argv"] for c in fake_bin.calls(tool)] for tool in _FAKE_TOOLS}


@pytest.mark.parametrize(
    "recipe",
    ["test", "test-int", "lint", "typecheck", "web-test", "web-lint", "db-new-migration", "e2e"],
)
def test_justfile_guarded_recipes_require_args(just: Just, fake_bin: FakeBin, recipe: str) -> None:
    result = just(recipe)

    assert result.returncode == 1, result.stderr
    assert "用法" in result.stderr
    assert _fake_calls(fake_bin) == {tool: [] for tool in _FAKE_TOOLS}


@pytest.mark.parametrize(
    "args",
    [
        ("test", "apps/api/tests"),
        ("test", "apps/api/tests/unit/"),
        ("lint", "scripts"),
        ("test-int", "apps/api/tests/integration"),
    ],
    ids=["test-tests", "test-unit-slash", "lint-scripts", "test-int-integration"],
)
def test_justfile_rejects_full_run_paths(
    just: Just, fake_bin: FakeBin, args: tuple[str, str]
) -> None:
    result = just(*args)

    assert result.returncode == 1, result.stderr
    assert "禁止全量" in result.stderr
    assert fake_bin.calls("uv") == []


def test_justfile_rejects_unknown_prefix(just: Just, fake_bin: FakeBin) -> None:
    result = just("test", "foo/bar.py")

    assert result.returncode == 1
    assert "只支援" in result.stderr
    assert fake_bin.calls("uv") == []


@pytest.mark.parametrize(
    "args",
    [("--linked",), ("--db-url", "x"), ("--yes", "--linked"), ("--yes", "--db-url", "x")],
    ids=["linked", "db-url", "yes-linked", "yes-db-url"],
)
def test_justfile_db_reset_rejects_linked(
    just: Just, fake_bin: FakeBin, args: tuple[str, ...]
) -> None:
    result = just("db-reset", *args)

    assert result.returncode == 1
    assert fake_bin.calls("supabase") == []


def test_justfile_db_reset_requires_yes_without_tty(just: Just, fake_bin: FakeBin) -> None:
    result = just("db-reset")

    assert result.returncode == 1
    assert "--yes" in result.stderr
    assert fake_bin.calls("supabase") == []


def test_justfile_db_new_migration_validates_name(
    just: Just, fake_bin: FakeBin, repo_root: Path
) -> None:
    bad = just("db-new-migration", "Bad-Name")

    assert bad.returncode == 1
    assert fake_bin.calls("supabase") == []

    good = just("db-new-migration", "create_probe")

    assert good.returncode == 0, good.stderr
    calls = fake_bin.calls("supabase")
    assert calls[0]["argv"] == ["migration", "new", "create_probe"]
    assert Path(calls[0]["cwd"]).resolve() == repo_root.resolve()


def _unused_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
    return port


def test_justfile_test_int_requires_local_db(just: Just, fake_bin: FakeBin) -> None:
    port = _unused_local_port()

    result = just(
        "test-int",
        "apps/api/tests/integration/x.py",
        env={"JUST_TEST_INT_DB_PORT": str(port)},
    )

    assert result.returncode == 1
    assert "just db-start" in result.stderr
    assert str(port) in result.stderr
    assert fake_bin.calls("uv") == []


def undocumented_recipes(list_output: str) -> list[str]:
    """`just --list` 輸出中沒有 `#` 說明的 recipe 名稱。"""
    missing: list[str] = []
    for line in list_output.splitlines():
        if not line.startswith("    "):
            continue
        if "#" not in line:
            missing.append(line.split()[0])
    return missing


def test_justfile_list_documents_every_recipe(just: Just) -> None:
    result = just("--list")

    assert result.returncode == 0, result.stderr
    recipes = [line.split()[0] for line in result.stdout.splitlines() if line.startswith("    ")]
    assert {"test", "test-int", "lint", "db-reset", "validate-tasks"} <= set(recipes)
    assert undocumented_recipes(result.stdout) == []
    # 檢查函式本身會抓到沒有說明的 recipe
    assert undocumented_recipes("Available recipes:\n    api # x\n    bare\n") == ["bare"]
