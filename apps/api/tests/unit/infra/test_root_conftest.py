"""INFRA-009：tests/conftest.py 的測試分層強制機制（自動 marker、marker 守衛、禁網）。

每個案例把真實的 tests/conftest.py 與 pyproject.toml 的 pytest 設定複製進 pytester 暫存專案，
以子行程執行（外層 unit 測試的 socket 限制不會影響內層 session）。
"""

import json
import textwrap
import tomllib
from pathlib import Path

import pytest

API_DIR = Path(__file__).resolve().parents[3]
ROOT_CONFTEST = API_DIR / "tests" / "conftest.py"
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
    return pytester


def _write(pytester: pytest.Pytester, rel_path: str, source: str) -> None:
    path = pytester.path / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(source), encoding="utf-8")


def test_root_conftest_marks_unit_by_directory(project: pytest.Pytester) -> None:
    _write(project, "tests/unit/test_a.py", "def test_a():\n    pass\n")

    project.runpytest_subprocess("-m", "unit").assert_outcomes(passed=1)
    project.runpytest_subprocess("-m", "integration").assert_outcomes(deselected=1)


def test_root_conftest_integration_excluded_by_default(project: pytest.Pytester) -> None:
    _write(project, "tests/integration/test_b.py", "def test_b():\n    pass\n")

    project.runpytest_subprocess().assert_outcomes(passed=0, deselected=1)
    project.runpytest_subprocess("-m", "integration").assert_outcomes(passed=1)


@pytest.mark.parametrize(
    "rel_path", ["tests/test_c.py", "tests/support/test_y.py"], ids=["tests-root", "support"]
)
def test_root_conftest_rejects_file_outside_layers(project: pytest.Pytester, rel_path: str) -> None:
    _write(project, rel_path, "def test_c():\n    pass\n")

    result = project.runpytest_subprocess()

    result.assert_outcomes(errors=1)
    result.stdout.fnmatch_lines(["*測試檔必須放在 tests/unit/ 或 tests/integration/*"])


@pytest.mark.parametrize(
    ("rel_path", "marker", "message"),
    [
        ("tests/unit/test_d.py", "integration", "tests/unit 底下不可標 integration"),
        ("tests/integration/test_d.py", "unit", "tests/integration 底下不可標 unit"),
    ],
    ids=["unit-dir", "integration-dir"],
)
def test_root_conftest_rejects_integration_marker_in_unit(
    project: pytest.Pytester, rel_path: str, marker: str, message: str
) -> None:
    _write(
        project,
        rel_path,
        f"""
        import pytest

        @pytest.mark.{marker}
        def test_d():
            pass
        """,
    )

    result = project.runpytest_subprocess()

    result.assert_outcomes(errors=1)
    result.stdout.fnmatch_lines([f"*{message}*"])


def test_root_conftest_unit_blocks_loopback(project: pytest.Pytester) -> None:
    _write(
        project,
        "tests/unit/test_e.py",
        """
        import socket

        def test_e():
            socket.create_connection(("127.0.0.1", 54342), timeout=1)
        """,
    )

    result = project.runpytest_subprocess()

    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*SocketBlockedError*"])


def test_root_conftest_integration_allows_only_loopback(project: pytest.Pytester) -> None:
    _write(
        project,
        "tests/integration/test_f.py",
        """
        import socket
        import socketserver
        import threading

        def test_f_remote():
            socket.create_connection(("10.255.255.1", 5432), timeout=1)

        def test_f_loopback():
            handler = socketserver.BaseRequestHandler
            with socketserver.TCPServer(("127.0.0.1", 0), handler) as server:
                thread = threading.Thread(target=server.handle_request, daemon=True)
                thread.start()
                port = server.server_address[1]
                with socket.create_connection(("127.0.0.1", port), timeout=1) as conn:
                    assert conn.getpeername()[1] == port
                thread.join(timeout=2)
        """,
    )

    result = project.runpytest_subprocess("-m", "integration")

    result.assert_outcomes(passed=1, failed=1)
    result.stdout.fnmatch_lines(["*SocketConnectBlockedError*10.255.255.1*"])
