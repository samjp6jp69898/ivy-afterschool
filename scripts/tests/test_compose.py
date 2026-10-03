"""INFRA-044：compose.yaml（本機 Postgres 17 + SeaweedFS S3）與 just db-start / db-stop 的結構測試。

結構測試直接解析 compose.yaml；db-start / db-stop 以 fake_bin 的假 docker 執行，不會真的啟動容器。
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import pytest
import yaml

if TYPE_CHECKING:
    from conftest import FakeBin, RunCmd

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE = REPO_ROOT / "compose.yaml"
S3_CONFIG_TARGET = "/etc/seaweedfs/s3.json"
_PINNED_IMAGES = {
    "db": re.compile(r"^postgres:17\.\d+"),
    "storage": re.compile(r"^chrislusf/seaweedfs:\d+\.\d+"),
}


def compose() -> dict[str, Any]:
    data = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def service(name: str) -> dict[str, Any]:
    result: dict[str, Any] = compose()["services"][name]
    return result


def flatten(value: Any) -> str:
    """把 compose 的 command / entrypoint / healthcheck.test（字串或清單）展開成單一字串。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return " ".join(str(v) for v in value)


def services_with_non_loopback_ports(services: dict[str, Any]) -> list[str]:
    bad: list[str] = []
    for name, spec in services.items():
        for port in spec.get("ports", []):
            if not str(port).startswith("127.0.0.1:"):
                bad.append(name)
                break
    return bad


def host_ports(services: dict[str, Any]) -> set[int]:
    return {
        int(str(port).split(":")[1]) for spec in services.values() for port in spec.get("ports", [])
    }


def services_with_floating_images(services: dict[str, Any]) -> list[str]:
    return [
        name
        for name, pattern in _PINNED_IMAGES.items()
        if not pattern.match(str(services.get(name, {}).get("image", "")))
    ]


def test_compose_ports_loopback_only() -> None:
    services = compose()["services"]

    assert services_with_non_loopback_ports(services) == []
    assert host_ports(services) == {54342, 54344}

    constructed = {"db": {"ports": ["54342:5432"]}, "storage": {"ports": ["127.0.0.1:54344:8333"]}}
    assert services_with_non_loopback_ports(constructed) == ["db"]


def test_compose_images_pinned() -> None:
    services = compose()["services"]

    assert services_with_floating_images(services) == []
    constructed = {
        "db": {"image": "postgres:latest"},
        "storage": {"image": "chrislusf/seaweedfs:latest"},
    }
    assert services_with_floating_images(constructed) == ["db", "storage"]


def test_compose_db_env_and_healthcheck() -> None:
    db = service("db")

    env = db["environment"]
    assert env["POSTGRES_USER"] == "postgres"
    assert env["POSTGRES_DB"] == "postgres"
    assert "pg_isready" in flatten(db["healthcheck"]["test"])
    assert not any("docker-entrypoint-initdb.d" in str(v) for v in db.get("volumes", []))


def _s3_identities() -> list[dict[str, Any]]:
    content = compose()["configs"]["seaweedfs-s3"]["content"]
    identities: list[dict[str, Any]] = json.loads(content)["identities"]
    return identities


def test_compose_storage_creates_private_bucket() -> None:
    storage = service("storage")

    command = flatten(storage["command"])
    assert "mini" in shlex.split(command)
    assert "-bucket=afterschool-local" in command
    assert f"-s3.config={S3_CONFIG_TARGET}" in command
    assert {"source": "seaweedfs-s3", "target": S3_CONFIG_TARGET} in storage["configs"]

    identities = _s3_identities()
    assert len(identities) == 1
    assert identities[0]["name"] == "afterschool"
    assert identities[0]["credentials"] == [
        {"accessKey": "afterschool", "secretKey": "afterschool-local-secret"}
    ]
    assert all(identity["name"] != "anonymous" for identity in identities)

    health = flatten(storage["healthcheck"]["test"])
    assert "/healthz" in health
    assert "/buckets/afterschool-local" in health


class DockerJust(Protocol):
    def __call__(self, recipe: str, docker_exit: int = 0) -> subprocess.CompletedProcess[str]: ...


@pytest.fixture
def docker_just(run_cmd: RunCmd, fake_bin: FakeBin, repo_root: Path) -> DockerJust:
    def _run(recipe: str, docker_exit: int = 0) -> subprocess.CompletedProcess[str]:
        fake_bin.add("docker", exit_code=docker_exit)
        # 守衛失效或舊版 recipe 時也只會呼叫到假指令，不會真的啟動服務
        fake_bin.add("supabase")
        return run_cmd(
            ["just", "--justfile", str(repo_root / "justfile"), recipe],
            env={"PATH": fake_bin.path_env()},
            input="",
        )

    return _run


def _docker_argvs(fake_bin: FakeBin) -> list[list[str]]:
    return [call["argv"] for call in fake_bin.calls("docker")]


def test_compose_db_start_invokes_compose(
    docker_just: DockerJust, fake_bin: FakeBin, repo_root: Path
) -> None:
    result = docker_just("db-start")

    assert result.returncode == 0, result.stderr
    compose_calls = [argv for argv in _docker_argvs(fake_bin) if "compose" in argv]
    assert len(compose_calls) == 1
    argv = compose_calls[0]
    assert argv[:3] == ["compose", "-f", str(repo_root / "compose.yaml")]
    up = argv.index("up")
    assert argv[up : up + 3] == ["up", "-d", "--wait"]
    assert {"db", "storage"} <= set(argv[up + 3 :])


def test_compose_db_start_requires_docker_daemon(
    docker_just: DockerJust, fake_bin: FakeBin
) -> None:
    result = docker_just("db-start", docker_exit=1)

    assert result.returncode == 1
    assert "Docker 未啟動" in result.stderr
    assert [argv for argv in _docker_argvs(fake_bin) if "compose" in argv] == []


def test_compose_db_stop_keeps_volumes(docker_just: DockerJust, fake_bin: FakeBin) -> None:
    result = docker_just("db-stop")

    assert result.returncode == 0, result.stderr
    argvs = _docker_argvs(fake_bin)
    assert any("compose" in argv and "stop" in argv for argv in argvs)
    assert all("down" not in argv and "-v" not in argv for argv in argvs)
