"""INFRA-017：scripts/dev_down.sh（just down）安全停止 dev_up.sh 啟動的服務。

API / Web 以 `python -m http.server` 當假服務，先由 dev_up.sh 啟動；DEV_UP_ROOT 指向 tmp_path。
just 以 fake_bin 替換，只記錄呼叫、不真的操作 docker。
"""

from __future__ import annotations

import os
import shlex
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import pytest

if TYPE_CHECKING:
    from conftest import FakeBin, RunCmd


class Script(Protocol):
    def __call__(self, *args: str) -> subprocess.CompletedProcess[str]: ...


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
                os.kill(pid, signal.SIGKILL)


@pytest.fixture
def env(fake_bin: FakeBin, root: Path) -> dict[str, str | None]:
    fake_bin.add("just")
    api_port, web_port = _free_port(), _free_port()
    return {
        "PATH": fake_bin.path_env(),
        "DEV_UP_ROOT": str(root),
        "DEV_UP_API_CMD": _server_cmd(api_port),
        "DEV_UP_WEB_CMD": _server_cmd(web_port),
        "DEV_UP_API_PORT": str(api_port),
        "DEV_UP_WEB_PORT": str(web_port),
        "DEV_UP_SKIP_DB": "1",
        "DEV_UP_WAIT_SECONDS": "15",
    }


@pytest.fixture
def dev_up(run_cmd: RunCmd, repo_root: Path, root: Path, env: dict[str, str | None]) -> Script:
    def _run(*args: str) -> subprocess.CompletedProcess[str]:
        return run_cmd(
            ["/bin/bash", str(repo_root / "scripts" / "dev_up.sh"), *args], cwd=root, env=env
        )

    return _run


@pytest.fixture
def dev_down(run_cmd: RunCmd, repo_root: Path, root: Path, env: dict[str, str | None]) -> Script:
    def _run(*args: str) -> subprocess.CompletedProcess[str]:
        return run_cmd(
            ["/bin/bash", str(repo_root / "scripts" / "dev_down.sh"), *args], cwd=root, env=env
        )

    return _run


def test_dev_down_stops_services_started_by_dev_up(
    dev_up: Script, dev_down: Script, root: Path
) -> None:
    up = dev_up()
    assert up.returncode == 0, up.stderr
    pids = [
        int((root / "var" / "run" / f"{name}.pid").read_text(encoding="utf-8").strip())
        for name in ("api", "web")
    ]
    assert all(_alive(pid) for pid in pids)

    result = dev_down()

    assert result.returncode == 0, result.stderr
    for pid in pids:
        assert not _alive(pid)
    for name in ("api", "web"):
        assert not (root / "var" / "run" / f"{name}.pid").exists()
        assert not (root / "var" / "run" / f"{name}.cmd").exists()


def test_dev_down_skips_reused_pid(dev_down: Script, root: Path) -> None:
    bystander = subprocess.Popen(["sleep", "60"])  # noqa: S607
    try:
        run_dir = root / "var" / "run"
        run_dir.mkdir(parents=True)
        (run_dir / "api.pid").write_text(f"{bystander.pid}\n", encoding="utf-8")
        (run_dir / "api.cmd").write_text("just api", encoding="utf-8")

        result = dev_down()

        assert result.returncode == 0, result.stderr
        assert bystander.poll() is None
        assert "已不是 api" in result.stdout + result.stderr
        assert not (run_dir / "api.pid").exists()
        assert not (run_dir / "api.cmd").exists()
    finally:
        bystander.kill()
        bystander.wait()


def test_dev_down_without_all_keeps_db(dev_down: Script, fake_bin: FakeBin) -> None:
    result = dev_down()

    assert result.returncode == 0, result.stderr
    assert all("db-stop" not in call["argv"] for call in fake_bin.calls("just"))


def test_dev_down_all_stops_db(dev_down: Script, fake_bin: FakeBin) -> None:
    result = dev_down("--all")

    assert result.returncode == 0, result.stderr
    calls = fake_bin.calls("just")
    assert [call["argv"] for call in calls] == [["db-stop"]]


def test_dev_down_rejects_unknown_flag(dev_down: Script) -> None:
    result = dev_down("--force")

    assert result.returncode == 1
    assert "用法" in result.stderr


def _port_open(port: int) -> bool:
    try:
        socket.create_connection(("127.0.0.1", port), timeout=1).close()
    except OSError:
        return False
    return True


def _wait_gone(pid: int, seconds: float = 5) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.1)
    return not _alive(pid)


def test_dev_down_stops_child_behind_non_exec_wrapper(
    dev_up: Script, dev_down: Script, env: dict[str, str | None], tmp_path: Path
) -> None:
    # 與 just api（bash → uv → uvicorn）同型：記錄的 pid 是包裝 shell，監聽 port 的是它的子行程
    port = int(env["DEV_UP_API_PORT"] or 0)
    child_pid_file = tmp_path / "child.pid"
    wrapper = tmp_path / "wrapper.sh"
    wrapper.write_text(
        f"#!/bin/sh\n{shlex.quote(sys.executable)} -m http.server $1 --bind 127.0.0.1 &\n"
        "echo $! > $2\nwait\n",
        encoding="utf-8",
    )
    env["DEV_UP_API_CMD"] = f"sh {wrapper} {port} {child_pid_file}"
    up = dev_up()
    assert up.returncode == 0, up.stderr
    child_pid = int(child_pid_file.read_text(encoding="utf-8").strip())
    assert _alive(child_pid)
    assert _port_open(port)

    result = dev_down()

    assert result.returncode == 0, result.stderr
    assert _wait_gone(child_pid)
    assert not _port_open(port)


def test_dev_down_reused_pid_leaves_process_group_alone(dev_down: Script, root: Path) -> None:
    leader = subprocess.Popen(["sleep", "60"], process_group=0)  # noqa: S607
    member = subprocess.Popen(["sleep", "60"], process_group=leader.pid)  # noqa: S607
    try:
        run_dir = root / "var" / "run"
        run_dir.mkdir(parents=True)
        (run_dir / "api.pid").write_text(f"{leader.pid}\n", encoding="utf-8")
        (run_dir / "api.cmd").write_text("just api", encoding="utf-8")

        result = dev_down()

        assert result.returncode == 0, result.stderr
        time.sleep(0.5)
        assert leader.poll() is None
        assert member.poll() is None
    finally:
        for proc in (leader, member):
            proc.kill()
            proc.wait()
