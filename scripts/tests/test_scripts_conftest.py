"""INFRA-008：scripts/tests 的 pytest.ini 與共用 fixture（run_cmd、fake_bin、local_db_url）。"""

from __future__ import annotations

import socket
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import pytest_socket

if TYPE_CHECKING:
    from conftest import FakeBin, RunCmd

TESTS_DIR = Path(__file__).resolve().parent


@pytest.fixture
def project(pytester: pytest.Pytester) -> pytest.Pytester:
    """複製真實 pytest.ini 與 conftest.py 的暫存專案。"""
    pytester.makefile(".ini", pytest=(TESTS_DIR / "pytest.ini").read_text(encoding="utf-8"))
    pytester.makeconftest((TESTS_DIR / "conftest.py").read_text(encoding="utf-8"))
    return pytester


def test_scripts_conftest_run_cmd_captures_output(run_cmd: RunCmd) -> None:
    result = run_cmd(["python3", "-c", 'import sys; print("hi"); sys.exit(3)'])

    assert result.returncode == 3
    assert result.stdout == "hi\n"


def test_scripts_conftest_run_cmd_env_none_removes_var(
    run_cmd: RunCmd, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("X_PROBE", "1")

    result = run_cmd(
        ["python3", "-c", 'import os; print(os.environ.get("X_PROBE", "absent"))'],
        env={"X_PROBE": None},
    )

    assert result.stdout == "absent\n"


def test_scripts_conftest_run_cmd_defaults_to_repo_root(run_cmd: RunCmd, repo_root: Path) -> None:
    result = run_cmd(["python3", "-c", "import os; print(os.getcwd())"])

    assert Path(result.stdout.strip()).resolve() == repo_root.resolve()
    assert (repo_root / "justfile").is_file()


def test_scripts_conftest_fake_bin_records_calls(
    run_cmd: RunCmd, fake_bin: FakeBin, tmp_path: Path
) -> None:
    fake_bin.add("supabase", stdout="ok", exit_code=4)

    result = run_cmd(
        ["supabase", "db", "push", "--dry-run"],
        cwd=tmp_path,
        env={"PATH": fake_bin.path_env()},
    )

    assert result.returncode == 4
    assert result.stdout == "ok"
    calls = fake_bin.calls("supabase")
    assert calls[0]["argv"] == ["db", "push", "--dry-run"]
    assert Path(calls[0]["cwd"]).resolve() == tmp_path.resolve()
    assert fake_bin.calls("docker") == []


def test_scripts_conftest_local_db_url_rejects_remote(
    project: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SCRIPTS_TEST_DATABASE_URL", "postgresql://u:p@10.0.0.5:5432/x")
    project.makepyfile(test_remote="def test_remote(local_db_url):\n    pass\n")

    result = project.runpytest_subprocess()

    # fixture 在 setup 階段 pytest.fail，計為 error
    result.assert_outcomes(errors=1)
    result.stdout.fnmatch_lines(["*只允許本機 loopback DB*10.0.0.5*"])


def test_scripts_conftest_db_tests_deselected_by_default(project: pytest.Pytester) -> None:
    project.makepyfile(
        test_db="""
        def test_db(local_db_conn):
            local_db_conn.execute("select 1")
        """
    )

    result = project.runpytest_subprocess()

    result.assert_outcomes(passed=0, deselected=1)


def test_scripts_conftest_blocks_external_socket() -> None:
    # 全域 --allow-hosts 下 pytest-socket 對非允許 host 拋 SocketConnectBlockedError；
    # 整個 socket 被停用時拋 SocketBlockedError（兩者互不為子類別）。
    with pytest.raises((pytest_socket.SocketBlockedError, pytest_socket.SocketConnectBlockedError)):
        socket.create_connection(("1.1.1.1", 53), timeout=1)
