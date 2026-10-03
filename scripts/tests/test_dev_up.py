"""INFRA-016：scripts/dev_up.sh（just up）啟動本機 DB / API / Web 並記錄 pid。

API / Web 以 `python -m http.server <臨時 port> --bind 127.0.0.1` 當假服務；DEV_UP_ROOT 指向
tmp_path，var/run、var/log 都寫在暫存目錄。teardown 依 pid 檔 kill 所有啟動的行程。
"""

from __future__ import annotations

import os
import shlex
import signal
import socket
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import pytest

if TYPE_CHECKING:
    from conftest import FakeBin, RunCmd


class DevUp(Protocol):
    def __call__(self, **env: str | None) -> subprocess.CompletedProcess[str]: ...


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
    return port


def _server_cmd(port: int) -> str:
    return f"{shlex.quote(sys.executable)} -m http.server {port} --bind 127.0.0.1"


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _read_pid(root: Path, name: str) -> int:
    return int((root / "var" / "run" / f"{name}.pid").read_text(encoding="utf-8").strip())


@pytest.fixture
def root(tmp_path: Path) -> Iterator[Path]:
    root = tmp_path / "repo"
    root.mkdir()
    yield root
    for name in ("api", "web"):
        pid_file = root / "var" / "run" / f"{name}.pid"
        if pid_file.exists():
            pid = int(pid_file.read_text(encoding="utf-8").strip())
            if _alive(pid):
                os.kill(pid, signal.SIGTERM)


@pytest.fixture
def dev_up(run_cmd: RunCmd, repo_root: Path, root: Path, fake_bin: FakeBin) -> DevUp:
    fake_bin.add("just")
    api_port, web_port = _free_port(), _free_port()

    def _run(**env: str | None) -> subprocess.CompletedProcess[str]:
        merged: dict[str, str | None] = {
            "PATH": fake_bin.path_env(),
            "DEV_UP_ROOT": str(root),
            "DEV_UP_API_CMD": _server_cmd(api_port),
            "DEV_UP_WEB_CMD": _server_cmd(web_port),
            "DEV_UP_API_PORT": str(api_port),
            "DEV_UP_WEB_PORT": str(web_port),
            "DEV_UP_SKIP_DB": "1",
            "DEV_UP_WAIT_SECONDS": "15",
        }
        merged.update(env)
        return run_cmd(
            ["/bin/bash", str(repo_root / "scripts" / "dev_up.sh")], cwd=root, env=merged
        )

    return _run


def test_dev_up_starts_services_and_writes_pids(dev_up: DevUp, root: Path) -> None:
    result = dev_up()

    assert result.returncode == 0, result.stdout + result.stderr
    for name in ("api", "web"):
        assert _alive(_read_pid(root, name)), name
        assert (root / "var" / "log" / f"{name}.log").exists()
    assert "http://127.0.0.1:5341/parent/" in result.stdout


def test_dev_up_is_idempotent(dev_up: DevUp, root: Path) -> None:
    first = dev_up()
    assert first.returncode == 0, first.stderr
    pid = _read_pid(root, "api")

    second = dev_up()

    assert second.returncode == 0, second.stderr
    assert "api 已在執行" in second.stdout
    assert _read_pid(root, "api") == pid


def test_dev_up_reports_crashed_service(dev_up: DevUp) -> None:
    result = dev_up(DEV_UP_API_CMD='sh -c "echo boom-marker >&2; exit 1"', DEV_UP_WAIT_SECONDS="2")

    assert result.returncode == 1
    assert "boom-marker" in result.stderr


def test_dev_up_replaces_stale_pid(dev_up: DevUp, root: Path) -> None:
    # 取得一個已結束行程的 pid
    stale = subprocess.Popen(["/usr/bin/true"])
    stale.wait()
    run_dir = root / "var" / "run"
    run_dir.mkdir(parents=True)
    (run_dir / "api.pid").write_text(f"{stale.pid}\n", encoding="utf-8")

    result = dev_up()

    assert result.returncode == 0, result.stderr
    new_pid = _read_pid(root, "api")
    assert new_pid != stale.pid
    assert _alive(new_pid)


def test_dev_up_starts_db_unless_skipped(dev_up: DevUp, fake_bin: FakeBin) -> None:
    started = dev_up(DEV_UP_SKIP_DB=None)

    assert started.returncode == 0, started.stderr
    calls = fake_bin.calls("just")
    assert calls[0]["argv"] == ["db-start"]

    (fake_bin.bin_dir / "just.calls.jsonl").unlink()
    skipped = dev_up(DEV_UP_SKIP_DB="1")

    assert skipped.returncode == 0, skipped.stderr
    assert all("db-start" not in call["argv"] for call in fake_bin.calls("just"))


def test_dev_up_rejects_port_taken_by_other_process(dev_up: DevUp, root: Path) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen()
        busy = sock.getsockname()[1]

        result = dev_up(DEV_UP_API_PORT=str(busy), DEV_UP_API_CMD=_server_cmd(busy))

    # 不可把別人占用的 port 誤當成自己的服務已就緒
    assert result.returncode == 1
    assert f"port {busy}" in result.stderr
    assert not (root / "var" / "run" / "api.pid").exists()


def test_dev_up_unrelated_process_with_same_suffix_is_not_ours(dev_up: DevUp, root: Path) -> None:
    # 無關行程：命令列以「 api」結尾，但不是 `just api`，也沒有監聽 API port
    decoy = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)", "api"])
    try:
        run_dir = root / "var" / "run"
        run_dir.mkdir(parents=True)
        (run_dir / "api.pid").write_text(f"{decoy.pid}\n", encoding="utf-8")
        (run_dir / "api.cmd").write_text("just api", encoding="utf-8")

        result = dev_up(DEV_UP_API_CMD="just api", DEV_UP_WAIT_SECONDS="2")

        assert result.returncode != 0
        assert "api 已在執行" not in result.stdout
        assert "全部就緒" not in result.stdout
    finally:
        decoy.kill()
        decoy.wait()


def test_dev_up_rerun_after_timeout_still_requires_port(dev_up: DevUp, root: Path) -> None:
    first = dev_up(DEV_UP_API_CMD="sleep 120", DEV_UP_WAIT_SECONDS="2")
    assert first.returncode == 1
    assert _alive(_read_pid(root, "api"))

    second = dev_up(DEV_UP_API_CMD="sleep 120", DEV_UP_WAIT_SECONDS="2")

    # 行程還活著、命令列也相符，但 port 從未開啟 → 不可視為就緒
    assert second.returncode == 1
    assert "全部就緒" not in second.stdout
    assert "沒有開始監聽" in second.stderr
