"""INFRA-006：apps/api/.env.example 與 apps/web/.env.example 的把關（env 只放基礎設施與 secret）。

解析 / 掃描函式接受路徑參數，所以同一個函式既檢查真實檔案、也以 tmp 檔驗證「會抓到違規」。
"""

import re
import textwrap
from pathlib import Path

import pytest

API_KEYS = {
    "APP_ENV",
    "DATABASE_URL",
    "APP_SECRET_KEY",
    "CORS_ORIGINS",
    "PUBLIC_BASE_URL",
    "SUPABASE_URL",
    "SUPABASE_SERVICE_ROLE_KEY",
    "SENTRY_DSN",
}
SECRET_PLACEHOLDER = "change-me"
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
    assert set(parse_env_example(api_env_example)) == API_KEYS

    extra = tmp_path / ".env.example"
    extra.write_text(api_env_example.read_text(encoding="utf-8") + "\n# x\nLINE_CHANNEL_TOKEN=x\n")
    unexpected = set(parse_env_example(extra)) - API_KEYS
    assert unexpected == {"LINE_CHANNEL_TOKEN"}, f"多出的 key：{sorted(unexpected)}"


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
    assert values["APP_SECRET_KEY"] == SECRET_PLACEHOLDER
    assert values["SUPABASE_SERVICE_ROLE_KEY"] == SECRET_PLACEHOLDER


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
