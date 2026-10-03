"""INFRA-006 / INFRA-049：apps/api 與 apps/web 的 .env.example 把關（env 只放基礎設施與 secret）。

解析 / 掃描函式接受路徑參數，所以同一個函式既檢查真實檔案、也以 tmp 檔驗證「會抓到違規」。
本機值必須與 repo root 的 compose.yaml（INFRA-044）一致。
"""

import json
import re
import shlex
import textwrap
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
import yaml

# architecture_decisions §4 的 env，順序即範本順序
API_KEY_ORDER = [
    "APP_ENV",
    "DATABASE_URL",
    "MIGRATION_DATABASE_URL",
    "APP_SECRET_KEY",
    "CORS_ORIGINS",
    "PUBLIC_BASE_URL",
    "R2_ENDPOINT_URL",
    "R2_ACCESS_KEY_ID",
    "R2_SECRET_ACCESS_KEY",
    "R2_BUCKET",
    "SENTRY_DSN",
]
API_KEYS = set(API_KEY_ORDER)
LOCAL_DB_HOSTPORT = "127.0.0.1:54342"
PLACEHOLDER_VALUE = "change-me"
_KEY_LINE = re.compile(r"^([A-Z_][A-Z0-9_]*)=(.*)$")
_VITE_ENV = re.compile(r"import\.meta\.env\.VITE_")


def parse_env_example(path: Path) -> dict[str, str]:
    """回傳 KEY=VALUE 行的 dict（順序即檔案順序）。"""
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _KEY_LINE.match(line)
        if match:
            result[match.group(1)] = match.group(2)
    return result


def keys_without_comment(path: Path) -> list[str]:
    """回傳上一行不是 `#` 註解的 key。"""
    lines = path.read_text(encoding="utf-8").splitlines()
    violations: list[str] = []
    for index, line in enumerate(lines):
        match = _KEY_LINE.match(line)
        if not match:
            continue
        previous = lines[index - 1] if index > 0 else ""
        if not previous.startswith("#"):
            violations.append(match.group(1))
    return violations


def find_vite_env_usages(src_dir: Path) -> list[Path]:
    """掃描 .ts / .vue 中 import.meta.env.VITE_ 的使用，回傳命中的檔案。"""
    hits: list[Path] = []
    for path in sorted(src_dir.rglob("*")):
        if path.suffix not in {".ts", ".vue"} or not path.is_file():
            continue
        if _VITE_ENV.search(path.read_text(encoding="utf-8")):
            hits.append(path)
    return hits


@pytest.fixture
def api_env_example(repo_root: Path) -> Path:
    return repo_root / "apps" / "api" / ".env.example"


@pytest.fixture
def web_env_example(repo_root: Path) -> Path:
    return repo_root / "apps" / "web" / ".env.example"


def test_env_examples_api_keys_exact(api_env_example: Path, tmp_path: Path) -> None:
    assert list(parse_env_example(api_env_example)) == API_KEY_ORDER

    extra = tmp_path / ".env.example"
    extra.write_text(api_env_example.read_text(encoding="utf-8") + "\n# x\nSUPABASE_URL=x\n")
    unexpected = set(parse_env_example(extra)) - API_KEYS
    assert unexpected == {"SUPABASE_URL"}, f"多出的 key：{sorted(unexpected)}"


def test_env_examples_api_each_key_has_comment(api_env_example: Path, tmp_path: Path) -> None:
    assert keys_without_comment(api_env_example) == []

    broken = tmp_path / ".env.example"
    broken.write_text(
        textwrap.dedent(
            """\
            # 有註解
            APP_ENV=development
            DATABASE_URL=postgresql://x

            APP_SECRET_KEY=change-me
            """
        ),
        encoding="utf-8",
    )
    assert keys_without_comment(broken) == ["DATABASE_URL", "APP_SECRET_KEY"]


def test_env_examples_api_secret_placeholder(api_env_example: Path) -> None:
    values = parse_env_example(api_env_example)
    assert values["APP_SECRET_KEY"] == PLACEHOLDER_VALUE


def _compose(repo_root: Path) -> dict[str, Any]:
    data = yaml.safe_load((repo_root / "compose.yaml").read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _command_tokens(command: object) -> list[str]:
    if isinstance(command, str):
        return shlex.split(command)
    assert isinstance(command, list)
    return [str(token) for token in command]


def local_value_mismatches(values: dict[str, str], compose: dict[str, Any]) -> list[str]:
    """回傳本機值與 compose.yaml 不一致的 key（每項都做完整相等比較，不用子字串）。"""
    storage = compose["services"]["storage"]
    mismatches: list[str] = []

    storage_host_ports = {int(str(port).split(":")[1]) for port in storage["ports"]}
    endpoint_port = urlsplit(values["R2_ENDPOINT_URL"]).port
    if endpoint_port != 54344 or endpoint_port not in storage_host_ports:
        mismatches.append("R2_ENDPOINT_URL")

    identities = json.loads(compose["configs"]["seaweedfs-s3"]["content"])["identities"]
    credentials = identities[0]["credentials"][0] if len(identities) == 1 else {}
    if values["R2_ACCESS_KEY_ID"] != credentials.get("accessKey"):
        mismatches.append("R2_ACCESS_KEY_ID")
    if values["R2_SECRET_ACCESS_KEY"] != credentials.get("secretKey"):
        mismatches.append("R2_SECRET_ACCESS_KEY")

    buckets = [
        token.removeprefix("-bucket=")
        for token in _command_tokens(storage["command"])
        if token.startswith("-bucket=")
    ]
    if buckets != [values["R2_BUCKET"]]:
        mismatches.append("R2_BUCKET")

    for key in ("DATABASE_URL", "MIGRATION_DATABASE_URL"):
        if urlsplit(values[key]).netloc.rsplit("@", 1)[-1] != LOCAL_DB_HOSTPORT:
            mismatches.append(key)
    return mismatches


def test_env_examples_local_values_match_compose(api_env_example: Path, repo_root: Path) -> None:
    values = parse_env_example(api_env_example)
    compose = _compose(repo_root)

    assert local_value_mismatches(values, compose) == []

    # bucket 名稱是 compose 值的前綴或延伸時也必須被抓到（不得以子字串比對）
    for bucket in ("afterschool", "afterschool-local-x"):
        assert local_value_mismatches({**values, "R2_BUCKET": bucket}, compose) == ["R2_BUCKET"]
    wrong_db = {**values, "DATABASE_URL": "postgresql+psycopg://u:p@127.0.0.1:5432/postgres"}
    assert local_value_mismatches(wrong_db, compose) == ["DATABASE_URL"]


def test_env_examples_web_has_no_keys(web_env_example: Path) -> None:
    assert web_env_example.exists()
    assert parse_env_example(web_env_example) == {}
    # 檔案要有說明（不是空檔）
    assert "/api/parent/config" in web_env_example.read_text(encoding="utf-8")


def test_env_examples_web_src_no_vite_env(repo_root: Path, tmp_path: Path) -> None:
    assert find_vite_env_usages(repo_root / "apps" / "web" / "src") == []

    src = tmp_path / "src"
    (src / "nested").mkdir(parents=True)
    offender = src / "nested" / "api.ts"
    offender.write_text("const base = import.meta.env.VITE_API\n", encoding="utf-8")
    (src / "ok.ts").write_text("const dev = import.meta.env.DEV\n", encoding="utf-8")
    assert find_vite_env_usages(src) == [offender]
