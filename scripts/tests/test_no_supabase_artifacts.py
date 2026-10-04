"""INFRA-047：以掃描鎖住 Supabase 時期產物不再回流。

掃描 `git ls-files` 列出的受版控檔案，排除 `docs/tasks/`（task 判決紀錄）與 `docs/mockups/`。
另有兩類檔案不掃：本檔（必須寫出被禁字串）與 NEGATIVE_ASSERTION_FILES（明確斷言「某輸出不含
舊名稱」的回歸測試，必須寫出被禁字串才能斷言）。新檔案一律受掃描。
"""

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

FORBIDDEN = ("supabase", "check_rls", "check-rls", "service_role")
EXCLUDED_PREFIXES = ("docs/tasks/", "docs/mockups/")
NEGATIVE_ASSERTION_FILES = frozenset(
    {
        "scripts/tests/test_no_supabase_artifacts.py",
        "scripts/tests/test_bootstrap.py",
        "scripts/tests/test_ci_workflow.py",
        "scripts/tests/test_deploy_configs.py",
        "scripts/tests/test_doctor.py",
        "scripts/tests/test_env_examples.py",
        "scripts/tests/test_readme.py",
        "apps/api/tests/integration/db/test_base.py",
        "apps/api/tests/integration/db/test_fixtures.py",
        "apps/api/tests/integration/infra/test_db_fixtures.py",
    }
)


def tracked_files(root: Path) -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"],  # noqa: S607
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return [line for line in out.splitlines() if line]


def find_violations(root: Path, rel_paths: list[str]) -> list[str]:
    """回傳內容（不分大小寫）含任一被禁字串的檔案相對路徑。"""
    bad: list[str] = []
    for rel in rel_paths:
        if rel.startswith(EXCLUDED_PREFIXES) or rel in NEGATIVE_ASSERTION_FILES:
            continue
        path = root / rel
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8").lower()
        except UnicodeDecodeError:
            continue
        if any(term in text for term in FORBIDDEN):
            bad.append(rel)
    return bad


def test_no_supabase_dir() -> None:
    assert not (REPO_ROOT / "supabase").exists()
    out = subprocess.run(
        ["git", "ls-files", "supabase"],  # noqa: S607
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert out.strip() == ""


def test_no_check_rls_artifacts(run_cmd) -> None:  # type: ignore[no-untyped-def]
    assert not (REPO_ROOT / "scripts" / "check_rls.py").exists()
    assert not (REPO_ROOT / "scripts" / "tests" / "test_check_rls.py").exists()
    result = run_cmd(["just", "--justfile", str(REPO_ROOT / "justfile"), "--list"])
    assert result.returncode == 0
    assert "check-rls" not in result.stdout


def test_no_supabase_mentions(tmp_path: Path) -> None:
    assert find_violations(REPO_ROOT, tracked_files(REPO_ROOT)) == []

    (tmp_path / "docs" / "tasks").mkdir(parents=True)
    (tmp_path / "docs" / "tasks" / "note.md").write_text("SUPABASE_URL=x\n", encoding="utf-8")
    (tmp_path / "leak.env").write_text("SUPABASE_URL=x\n", encoding="utf-8")
    (tmp_path / "clean.txt").write_text("nothing here\n", encoding="utf-8")
    rels = ["docs/tasks/note.md", "leak.env", "clean.txt"]

    assert find_violations(tmp_path, rels) == ["leak.env"]


@pytest.mark.parametrize("rel", ["apps/api/pyproject.toml", "scripts/tests/pytest.ini"])
def test_pytest_marker_text(rel: str) -> None:
    text = (REPO_ROOT / rel).read_text(encoding="utf-8")
    marker_lines = [line for line in text.splitlines() if "integration:" in line]

    assert len(marker_lines) == 1
    assert "127.0.0.1:54342" in marker_lines[0]
    assert "127.0.0.1:54344" in marker_lines[0]
    assert "supabase" not in text.lower()
