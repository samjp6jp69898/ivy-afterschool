"""INFRA-019：scripts/hooks/pre-commit 只對 staged 檔案跑 lint，並擋下 .env 與不合格的 tasks.json。

每個案例在 tmp_path 以 git init 建立暫存 repo，複製 hook 與 scripts/validate_tasks.py，用 fake_bin
替換 just，stage 檔案後直接執行 hook 腳本。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import pytest

if TYPE_CHECKING:
    from conftest import FakeBin, RunCmd

# 在 git hook 內執行本測試時，這些變數會讓暫存 repo 的 git 指令改操作外層 repo
_GIT_ENV_RESET: dict[str, str | None] = {
    "GIT_DIR": None,
    "GIT_INDEX_FILE": None,
    "GIT_WORK_TREE": None,
    "GIT_OBJECT_DIRECTORY": None,
}


class Repo(Protocol):
    path: Path

    def write(self, rel_path: str, content: str = "x = 1\n") -> None: ...
    def git(self, *args: str) -> subprocess.CompletedProcess[str]: ...
    def hook(self) -> subprocess.CompletedProcess[str]: ...


class _Repo:
    def __init__(self, path: Path, run_cmd: RunCmd, fake_bin: FakeBin) -> None:
        self.path = path
        self._run = run_cmd
        self._fake_bin = fake_bin

    def write(self, rel_path: str, content: str = "x = 1\n") -> None:
        target = self.path / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def git(self, *args: str) -> subprocess.CompletedProcess[str]:
        result = self._run(["git", *args], cwd=self.path, env=_GIT_ENV_RESET)
        assert result.returncode == 0, result.stderr
        return result

    def hook(self) -> subprocess.CompletedProcess[str]:
        return self._run(
            [str(self.path / "scripts" / "hooks" / "pre-commit")],
            cwd=self.path,
            env={**_GIT_ENV_RESET, "PATH": self._fake_bin.path_env()},
        )


@pytest.fixture
def repo(tmp_path: Path, run_cmd: RunCmd, fake_bin: FakeBin, repo_root: Path) -> Repo:
    fake_bin.add("just")
    path = tmp_path / "repo"
    (path / "scripts" / "hooks").mkdir(parents=True)
    shutil.copy2(repo_root / "scripts" / "hooks" / "pre-commit", path / "scripts" / "hooks")
    shutil.copy2(repo_root / "scripts" / "validate_tasks.py", path / "scripts")
    r = _Repo(path, run_cmd, fake_bin)
    r.git("init", "-q")
    r.git("config", "user.email", "probe@example.com")
    r.git("config", "user.name", "probe")
    r.git("config", "commit.gpgsign", "false")
    return r


def _just_argvs(fake_bin: FakeBin) -> list[list[str]]:
    return [call["argv"] for call in fake_bin.calls("just")]


def test_pre_commit_lints_staged_python(repo: Repo, fake_bin: FakeBin) -> None:
    repo.write("apps/api/app/x.py")
    repo.write("scripts/y.py")
    repo.git("add", "apps/api/app/x.py", "scripts/y.py")

    result = repo.hook()

    assert result.returncode == 0, result.stderr
    # justfile 不允許 apps/api/ 與 scripts/ 混在同一次呼叫，依路由各呼叫一次
    assert _just_argvs(fake_bin) == [["lint", "apps/api/app/x.py"], ["lint", "scripts/y.py"]]


def test_pre_commit_lints_staged_web_files(repo: Repo, fake_bin: FakeBin) -> None:
    repo.write("apps/web/src/a.vue", "<template><div /></template>\n")
    repo.write("apps/web/src/components.d.ts", "export {}\n")
    repo.git("add", "apps/web/src/a.vue", "apps/web/src/components.d.ts")

    result = repo.hook()

    assert result.returncode == 0, result.stderr
    assert _just_argvs(fake_bin) == [["web-lint", "src/a.vue"]]


def test_pre_commit_rejects_env_file(repo: Repo, fake_bin: FakeBin) -> None:
    repo.write("apps/api/.env", "APP_SECRET_KEY=x\n")
    repo.write("apps/api/app/x.py")
    repo.git("add", "apps/api/.env", "apps/api/app/x.py")

    result = repo.hook()

    assert result.returncode == 1
    assert "拒絕提交 env 檔：apps/api/.env" in result.stderr
    assert fake_bin.calls("just") == []


@pytest.mark.parametrize(
    "rel_path", ["apps/web/.env.local", ".env.production"], ids=["dot-local", "root-prod"]
)
def test_pre_commit_rejects_env_variants(repo: Repo, fake_bin: FakeBin, rel_path: str) -> None:
    repo.write(rel_path, "X=1\n")
    repo.git("add", "-f", rel_path)

    result = repo.hook()

    assert result.returncode == 1
    assert f"拒絕提交 env 檔：{rel_path}" in result.stderr


@pytest.mark.parametrize(
    "rel_path", ["apps/api/.env.example", "apps/web/.env.local.example"], ids=["plain", "nested"]
)
def test_pre_commit_allows_env_example(repo: Repo, fake_bin: FakeBin, rel_path: str) -> None:
    repo.write(rel_path, "APP_SECRET_KEY=change-me\n")
    repo.git("add", rel_path)

    result = repo.hook()

    assert result.returncode == 0, result.stderr
    assert fake_bin.calls("just") == []


def test_pre_commit_skips_deleted_files(repo: Repo, fake_bin: FakeBin) -> None:
    repo.write("apps/api/app/old.py")
    repo.git("add", "apps/api/app/old.py")
    repo.git("-c", "core.hooksPath=/dev/null", "commit", "-q", "-m", "init")
    repo.git("rm", "-q", "apps/api/app/old.py")

    result = repo.hook()

    assert result.returncode == 0, result.stderr
    assert fake_bin.calls("just") == []


def test_pre_commit_fails_when_lint_fails(repo: Repo, fake_bin: FakeBin) -> None:
    fake_bin.add("just", exit_code=1)
    repo.write("apps/api/app/x.py")
    repo.git("add", "apps/api/app/x.py")

    result = repo.hook()

    assert result.returncode == 1
    assert _just_argvs(fake_bin) == [["lint", "apps/api/app/x.py"]]


def test_pre_commit_validates_tasks_json(repo: Repo) -> None:
    task: dict[str, object] = {
        "id": "INFRA-001",
        "title": "probe",
        "granularity_unit": "config",
        "target_path": "justfile",
        "source_ref": None,
        "status": "pending",
        "assignee_session": None,
        "depends_on": [],
        "suggested_model": "sonnet-5",
        "risk_notes": "",
        "open_design_questions": [],
        "tdd": {"test_path": None, "markers": [], "red_cases": [], "run": "", "notes": ""},
        "review": {"status": "pending", "reviewer": None, "notes": ""},
    }
    doc = {"area": "infra", "version": 1, "last_updated": "2026-10-03T00:00:00Z", "tasks": [task]}
    repo.write("docs/tasks/infra/tasks.json", json.dumps(doc, ensure_ascii=False, indent=2))
    repo.git("add", "docs/tasks/infra/tasks.json")

    result = repo.hook()

    assert result.returncode == 1
    assert "description" in result.stdout + result.stderr


def test_pre_commit_no_relevant_files(repo: Repo, fake_bin: FakeBin) -> None:
    repo.write("README.md", "# probe\n")
    repo.git("add", "README.md")

    result = repo.hook()

    assert result.returncode == 0, result.stderr
    assert fake_bin.calls("just") == []
