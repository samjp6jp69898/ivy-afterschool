"""INFRA-018：scripts/doctor.sh 的本機環境檢查（工具版本、Docker、目錄、.env、port）。

工具一律以 fake_bin 假指令提供，PATH 只含假指令目錄與 /usr/bin:/bin；DOCTOR_ROOT 指向 tmp_path
底下的假 repo，不讀真實的 apps/api/.env。
"""

from __future__ import annotations

import socket
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import pytest

if TYPE_CHECKING:
    import subprocess

    from conftest import FakeBin, RunCmd

GOOD_SECRET = "s" * 40
_TOOL_VERSIONS = {
    "just": "just 1.43.0",
    "uv": "uv 0.9.0",
    "node": "v24.21.0",
    "pnpm": "11.25.0",
}
_COMPOSE_VERSION = "Docker Compose version v2.39.2"

# 假 docker：`info` 與 `compose version` 各自回指定的 exit code 與輸出
_FAKE_DOCKER = """#!{python}
import sys
args = sys.argv[1:]
if args[:1] == ["info"]:
    sys.exit({info_exit})
if args[:2] == ["compose", "version"]:
    print({compose_stdout!r})
    sys.exit({compose_exit})
sys.exit(0)
"""


def add_fake_docker(
    fake_bin: FakeBin,
    *,
    info_exit: int = 0,
    compose_exit: int = 0,
    compose_stdout: str = _COMPOSE_VERSION,
) -> None:
    path = fake_bin.bin_dir / "docker"
    path.write_text(
        _FAKE_DOCKER.format(
            python=sys.executable,
            info_exit=info_exit,
            compose_exit=compose_exit,
            compose_stdout=compose_stdout,
        ),
        encoding="utf-8",
    )
    path.chmod(0o755)


class Doctor(Protocol):
    def __call__(self, *args: str) -> subprocess.CompletedProcess[str]: ...


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
    return port


@pytest.fixture
def doctor_root(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "apps" / "api" / ".venv").mkdir(parents=True)
    (root / "apps" / "web" / "node_modules").mkdir(parents=True)
    (root / "apps" / "api" / ".env").write_text(
        f"APP_ENV=development\nAPP_SECRET_KEY={GOOD_SECRET}\nR2_BUCKET=afterschool-local\n",
        encoding="utf-8",
    )
    return root


@pytest.fixture
def tools(fake_bin: FakeBin) -> FakeBin:
    for name, version in _TOOL_VERSIONS.items():
        fake_bin.add(name, stdout=version + "\n")
    add_fake_docker(fake_bin)
    return fake_bin


@pytest.fixture
def doctor(run_cmd: RunCmd, repo_root: Path, tools: FakeBin, doctor_root: Path) -> Doctor:
    api_port = str(_free_port())
    web_port = str(_free_port())

    def _doctor(*args: str) -> subprocess.CompletedProcess[str]:
        return run_cmd(
            ["/bin/bash", str(repo_root / "scripts" / "doctor.sh"), *args],
            env={
                "PATH": f"{tools.bin_dir}:/usr/bin:/bin",
                "DOCTOR_ROOT": str(doctor_root),
                "DOCTOR_API_PORT": api_port,
                "DOCTOR_WEB_PORT": web_port,
            },
        )

    return _doctor


def _lines_with(stdout: str, *words: str) -> list[str]:
    return [line for line in stdout.splitlines() if all(w in line for w in words)]


def test_doctor_all_ok(doctor: Doctor) -> None:
    result = doctor()

    assert result.returncode == 0, result.stdout
    assert "FAIL" not in result.stdout
    assert len(_lines_with(result.stdout, "OK", "node")) >= 1


def test_doctor_wrong_node_major(doctor: Doctor, tools: FakeBin) -> None:
    tools.add("node", stdout="v22.11.0\n")

    result = doctor()

    assert result.returncode == 1, result.stdout
    assert len(_lines_with(result.stdout, "FAIL", "node")) == 1


def test_doctor_docker_not_running(doctor: Doctor, tools: FakeBin) -> None:
    add_fake_docker(tools, info_exit=1)

    result = doctor()

    assert result.returncode == 1, result.stdout
    assert len(_lines_with(result.stdout, "FAIL", "docker")) == 1


def test_doctor_placeholder_secret(doctor: Doctor, doctor_root: Path) -> None:
    (doctor_root / "apps" / "api" / ".env").write_text(
        "APP_ENV=development\nAPP_SECRET_KEY=change-me\n",
        encoding="utf-8",
    )

    result = doctor()

    assert result.returncode == 1, result.stdout
    assert len(_lines_with(result.stdout, "FAIL", "APP_SECRET_KEY")) == 1


def test_doctor_counts_multiple_failures(doctor: Doctor, tools: FakeBin) -> None:
    (tools.bin_dir / "pnpm").unlink()
    add_fake_docker(tools, compose_exit=1)

    result = doctor()

    assert result.returncode == 2, result.stdout
    assert len(_lines_with(result.stdout, "FAIL", "pnpm")) == 1
    assert len(_lines_with(result.stdout, "FAIL", "docker compose")) == 1
    assert _lines_with(result.stdout, "FAIL", "docker daemon") == []


def test_doctor_old_compose_major_fails(doctor: Doctor, tools: FakeBin) -> None:
    add_fake_docker(tools, compose_stdout="docker-compose version 1.29.2")

    result = doctor()

    assert result.returncode == 1, result.stdout
    assert len(_lines_with(result.stdout, "FAIL", "docker compose")) == 1


def test_doctor_no_supabase_check(doctor: Doctor) -> None:
    result = doctor()

    assert result.returncode == 0, result.stdout
    assert "supabase" not in result.stdout.lower()
    problems = [line for line in result.stdout.splitlines() if line.startswith(("WARN", "FAIL"))]
    # .env 沒有 SUPABASE_ key：唯一可能的 WARN 只剩本機服務未啟動的 port 探測
    assert all("port" in line for line in problems), problems


def test_doctor_probes_local_db_and_s3_ports(doctor: Doctor) -> None:
    result = doctor()

    assert len(_lines_with(result.stdout, "54342")) == 1
    assert len(_lines_with(result.stdout, "54344")) == 1


@pytest.fixture
def busy_port() -> Iterator[int]:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen()
        yield sock.getsockname()[1]


def test_doctor_port_busy_is_warning(
    run_cmd: RunCmd, repo_root: Path, tools: FakeBin, doctor_root: Path, busy_port: int
) -> None:
    result = run_cmd(
        ["/bin/bash", str(repo_root / "scripts" / "doctor.sh")],
        env={
            "PATH": f"{tools.bin_dir}:/usr/bin:/bin",
            "DOCTOR_ROOT": str(doctor_root),
            "DOCTOR_API_PORT": str(busy_port),
            "DOCTOR_WEB_PORT": str(_free_port()),
        },
    )

    port_lines = _lines_with(result.stdout, str(busy_port))
    assert len(port_lines) == 1, result.stdout
    assert port_lines[0].startswith("WARN")
    assert result.returncode == 0, result.stdout


def test_doctor_help_and_unknown_args(doctor: Doctor) -> None:
    help_result = doctor("--help")
    assert help_result.returncode == 0
    assert "doctor" in help_result.stdout

    bad = doctor("--bogus")
    assert bad.returncode == 1
    assert "--bogus" in bad.stderr
