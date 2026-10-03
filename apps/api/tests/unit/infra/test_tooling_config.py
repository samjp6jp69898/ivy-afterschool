"""INFRA-042：ruff isort first-party、TestClient 棄用警告、統一 anyio_backend 的工具設定。

pytester 案例把真實的 tests/conftest.py、tests/support 與 pyproject.toml 的 pytest 設定複製進
暫存專案，以子行程執行（不受外層 session 的設定與 socket 限制影響）。
"""

import json
import subprocess
import sys
import textwrap
import tomllib
from pathlib import Path

import pytest

API_DIR = Path(__file__).resolve().parents[3]
REPO_ROOT = API_DIR.parent.parent
ROOT_CONFTEST = API_DIR / "tests" / "conftest.py"
SUPPORT_DIR = API_DIR / "tests" / "support"
PYPROJECT = API_DIR / "pyproject.toml"


def _pytest_ini_toml() -> str:
    options = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["tool"]["pytest"]["ini_options"]
    lines = ["[tool.pytest.ini_options]"]
    lines += [f"{key} = {json.dumps(value, ensure_ascii=False)}" for key, value in options.items()]
    return "\n".join(lines) + "\n"


@pytest.fixture
def project(pytester: pytest.Pytester) -> pytest.Pytester:
    pytester.makepyprojecttoml(_pytest_ini_toml())
    tests_dir = pytester.path / "tests"
    tests_dir.mkdir()
    (tests_dir / "conftest.py").write_text(
        ROOT_CONFTEST.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (tests_dir / "support").mkdir()
    for module in SUPPORT_DIR.glob("*.py"):
        (tests_dir / "support" / module.name).write_text(
            module.read_text(encoding="utf-8"), encoding="utf-8"
        )
    return pytester


def _write(pytester: pytest.Pytester, rel_path: str, source: str) -> None:
    path = pytester.path / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(source), encoding="utf-8")


def _fixed_source_from_diff(diff: str) -> list[str]:
    """由 unified diff 還原修正後的檔案內容（保留 context 與新增行）。"""
    lines: list[str] = []
    for line in diff.splitlines():
        if line.startswith(("+++", "---", "@@")):
            continue
        if line.startswith(("+", " ")):
            lines.append(line[1:])
        elif line == "":
            lines.append("")
    while lines and lines[-1] == "":
        lines.pop()
    return lines


def test_tooling_isort_app_is_first_party(tmp_path: Path) -> None:
    probe = tmp_path / "probe.py"
    probe.write_text("import os\nimport app.core.errors\nimport pydantic\n", encoding="utf-8")
    ruff = Path(sys.executable).parent / "ruff"

    result = subprocess.run(  # noqa: S603  固定參數呼叫 venv 內的 ruff
        [
            str(ruff),
            "check",
            "--no-cache",
            "--config",
            "apps/api/pyproject.toml",
            "--select",
            "I",
            "--diff",
            str(probe),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    fixed = _fixed_source_from_diff(result.stdout)
    assert fixed == ["import os", "", "import pydantic", "", "import app.core.errors"], (
        result.stdout + result.stderr
    )


def test_tooling_testclient_no_deprecation_warning(project: pytest.Pytester) -> None:
    _write(
        project,
        "tests/unit/test_client.py",
        """
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        def test_client():
            response = TestClient(FastAPI()).get("/")
            assert response.status_code == 404
        """,
    )

    # StarletteDeprecationWarning 繼承 UserWarning 而非 DeprecationWarning，兩類都要升級成錯誤
    result = project.runpytest_subprocess(
        "-W",
        "error::DeprecationWarning",
        "-W",
        "error::starlette.exceptions.StarletteDeprecationWarning",
    )

    result.assert_outcomes(passed=1)


def test_tooling_deprecation_filter_not_too_broad(project: pytest.Pytester) -> None:
    _write(
        project,
        "tests/unit/test_warn.py",
        """
        import warnings

        def test_warn():
            warnings.warn("x", DeprecationWarning, stacklevel=1)
        """,
    )

    result = project.runpytest_subprocess("-W", "error::DeprecationWarning")

    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*DeprecationWarning: x*"])


def test_tooling_anyio_backend_defaults_to_asyncio(project: pytest.Pytester) -> None:
    _write(
        project,
        "tests/unit/test_async.py",
        """
        import pytest

        # session scope 的 fixture 只能依賴 session scope 的 anyio_backend（否則 ScopeMismatch）
        @pytest.fixture(scope="session")
        def backend_seen_by_session(anyio_backend):
            return anyio_backend

        @pytest.mark.anyio
        async def test_async(anyio_backend, backend_seen_by_session):
            assert anyio_backend == "asyncio"
            assert backend_seen_by_session == "asyncio"
        """,
    )

    collected = project.runpytest_subprocess("--collect-only", "-q")
    item_lines = [line for line in collected.outlines if "::" in line]
    # 根 conftest 的 anyio_backend 不參數化：nodeid 不帶 [asyncio] / [trio] 之類的後綴
    assert item_lines == ["tests/unit/test_async.py::test_async"], collected.outlines

    project.runpytest_subprocess().assert_outcomes(passed=1)
