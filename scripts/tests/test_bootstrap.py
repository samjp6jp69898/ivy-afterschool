"""INFRA-015：scripts/bootstrap.sh 的冪等初始化（依賴安裝、apps/api/.env、下一步提示）。

每個案例在 tmp_path 建立最小 repo（複製 bootstrap.sh 與兩份 .env.example、空的 apps/api 與
apps/web），以 fake_bin 替換 uv / pnpm / just；PATH 只含假指令、apps/api/.venv/bin（提供 python3）
與 /usr/bin:/bin，不會碰到真實的 uv / pnpm。
"""

from __future__ import annotations

import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import pytest

if TYPE_CHECKING:
    from conftest import FakeBin, RunCmd

_VENV_BIN = Path(sys.executable).parent
PLACEHOLDER_SECRET = "change-me"  # noqa: S105  範本的佔位值，不是密碼


class Bootstrap(Protocol):
    def __call__(self, root: Path) -> subprocess.CompletedProcess[str]: ...


def _make_repo(base: Path, repo_root: Path) -> Path:
    root = base
    (root / "scripts").mkdir(parents=True)
    shutil.copy2(repo_root / "scripts" / "bootstrap.sh", root / "scripts" / "bootstrap.sh")
    for app in ("api", "web"):
        (root / "apps" / app).mkdir(parents=True)
        shutil.copy(repo_root / "apps" / app / ".env.example", root / "apps" / app / ".env.example")
    return root


@pytest.fixture
def repo(tmp_path: Path, repo_root: Path) -> Path:
    return _make_repo(tmp_path / "repo", repo_root)


@pytest.fixture
def bootstrap(run_cmd: RunCmd, fake_bin: FakeBin) -> Bootstrap:
    for tool in ("uv", "pnpm", "just"):
        fake_bin.add(tool)

    def _run(root: Path) -> subprocess.CompletedProcess[str]:
        return run_cmd(
            ["/bin/bash", str(root / "scripts" / "bootstrap.sh")],
            cwd=root,
            env={
                "PATH": f"{fake_bin.bin_dir}:{_VENV_BIN}:/usr/bin:/bin",
                "BOOTSTRAP_ROOT": str(root),
            },
        )

    return _run


def _env_lines(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            result[key] = value
    return result


def test_bootstrap_creates_env_with_random_secret(bootstrap: Bootstrap, repo: Path) -> None:
    result = bootstrap(repo)

    assert result.returncode == 0, result.stderr
    env_file = repo / "apps" / "api" / ".env"
    values = _env_lines(env_file)
    secret = values["APP_SECRET_KEY"]
    assert len(secret) >= 48
    assert secret != PLACEHOLDER_SECRET
    example = _env_lines(repo / "apps" / "api" / ".env.example")
    assert {k: v for k, v in values.items() if k != "APP_SECRET_KEY"} == {
        k: v for k, v in example.items() if k != "APP_SECRET_KEY"
    }
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600


def test_bootstrap_secret_differs_between_runs(
    bootstrap: Bootstrap, tmp_path: Path, repo_root: Path
) -> None:
    first = _make_repo(tmp_path / "a", repo_root)
    second = _make_repo(tmp_path / "b", repo_root)

    assert bootstrap(first).returncode == 0
    assert bootstrap(second).returncode == 0

    secret_a = _env_lines(first / "apps" / "api" / ".env")["APP_SECRET_KEY"]
    secret_b = _env_lines(second / "apps" / "api" / ".env")["APP_SECRET_KEY"]
    assert secret_a != secret_b


def test_bootstrap_keeps_existing_env(bootstrap: Bootstrap, repo: Path) -> None:
    env_file = repo / "apps" / "api" / ".env"
    env_file.write_bytes(b"APP_ENV=production\n")

    result = bootstrap(repo)

    assert result.returncode == 0, result.stderr
    assert env_file.read_bytes() == b"APP_ENV=production\n"
    assert "已存在，略過" in result.stdout


def test_bootstrap_invokes_package_managers(
    bootstrap: Bootstrap, repo: Path, fake_bin: FakeBin
) -> None:
    result = bootstrap(repo)

    assert result.returncode == 0, result.stderr
    uv_calls = [c for c in fake_bin.calls("uv") if c["argv"] == ["sync", "--frozen"]]
    assert len(uv_calls) == 1
    assert Path(uv_calls[0]["cwd"]).resolve() == (repo / "apps" / "api").resolve()
    pnpm_calls = [
        c for c in fake_bin.calls("pnpm") if c["argv"] == ["install", "--frozen-lockfile"]
    ]
    assert len(pnpm_calls) == 1
    assert Path(pnpm_calls[0]["cwd"]).resolve() == (repo / "apps" / "web").resolve()


def test_bootstrap_missing_uv_fails(bootstrap: Bootstrap, repo: Path, fake_bin: FakeBin) -> None:
    (fake_bin.bin_dir / "uv").unlink()

    result = bootstrap(repo)

    assert result.returncode == 1
    assert "找不到 uv" in result.stderr
    assert not (repo / "apps" / "api" / ".env").exists()
    assert fake_bin.calls("pnpm") == []


def test_bootstrap_prints_next_steps(bootstrap: Bootstrap, repo: Path) -> None:
    result = bootstrap(repo)

    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert out.index("just db-start") < out.index("just db-reset --yes") < out.index("just up")
    assert "supabase" not in out.lower()


def test_bootstrap_installs_hooks_when_recipe_exists(
    bootstrap: Bootstrap, repo: Path, fake_bin: FakeBin
) -> None:
    (repo / "justfile").write_text("install-hooks:\n    true\n", encoding="utf-8")
    fake_bin.add("just", stdout="install-hooks up\n")

    result = bootstrap(repo)

    assert result.returncode == 0, result.stderr
    assert any(c["argv"][-1] == "install-hooks" for c in fake_bin.calls("just"))
