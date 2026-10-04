"""INFRA-040：docs/deployment.md 與 env 範本、Railway 設定同步的把關。

檢查函式接受內容 / 路徑參數，同一個函式既檢查真實檔案、也以合成輸入驗證「會抓到違規」。
"""

import json
import re
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DOC = REPO_ROOT / "docs" / "deployment.md"
API_ENV = REPO_ROOT / "apps" / "api" / ".env.example"
API_RAILWAY = REPO_ROOT / "apps" / "api" / "railway.json"
WEB_RAILWAY = REPO_ROOT / "apps" / "web" / "railway.json"


def _doc() -> str:
    return DOC.read_text(encoding="utf-8")


def _env_keys(text: str) -> set[str]:
    return set(re.findall(r"^([A-Z][A-Z0-9_]*)=", text, flags=re.MULTILINE))


def _railway(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def missing_env_keys(env_text: str, doc: str) -> list[str]:
    """回傳在範本中、但文件沒提到的 key（依字母排序）。"""
    return sorted(key for key in _env_keys(env_text) if key not in doc)


def env_section(doc: str) -> str:
    """取出「環境變數」章節（從含「環境變數」的 `## ` 標題到下一個 `## `）。"""
    match = re.search(
        r"^## [^\n]*環境變數[^\n]*\n(.*?)(?=^## |\Z)", doc, flags=re.MULTILINE | re.DOTALL
    )
    assert match, "deployment.md 缺少「環境變數」章節"
    return match.group(1)


def test_deployment_doc_lists_all_api_env() -> None:
    assert missing_env_keys(API_ENV.read_text(encoding="utf-8"), _doc()) == []
    extra = API_ENV.read_text(encoding="utf-8") + "\nBRAND_NEW_KEY=1\n"
    assert missing_env_keys(extra, _doc()) == ["BRAND_NEW_KEY"]


def test_deployment_doc_lists_web_vars() -> None:
    doc = _doc()
    for token in ("BACKEND_URL", "RAILWAY_PRIVATE_DOMAIN", "TRUSTED_EDGE_CIDRS"):
        assert token in doc, token


def test_deployment_doc_references_commands() -> None:
    doc = _doc()
    for token in (
        "just smoke",
        "app.cli create-admin",
        "app.cli migrate",
        "preDeployCommand",
        "Wait for CI",
    ):
        assert token in doc, token


def test_deployment_doc_pre_deploy_matches_railway_json() -> None:
    pre = _railway(API_RAILWAY)["deploy"]["preDeployCommand"]
    commands = [pre] if isinstance(pre, str) else list(pre)
    assert commands
    doc = _doc()
    for command in commands:
        assert command in doc, command


def test_deployment_doc_healthcheck_paths_match() -> None:
    doc = _doc()
    paths = {
        _railway(API_RAILWAY)["deploy"]["healthcheckPath"],
        _railway(WEB_RAILWAY)["deploy"]["healthcheckPath"],
    }
    assert paths == {"/api/health", "/healthz"}
    for path in paths:
        assert path in doc, path


def test_deployment_doc_single_replica_note() -> None:
    assert _railway(API_RAILWAY)["deploy"]["numReplicas"] == 1
    doc = _doc()
    assert "numReplicas" in doc
    assert re.search(r"numReplicas[^\n]{0,20}\b1\b", doc), "numReplicas 後應明寫值 1"


def test_deployment_doc_database_setup() -> None:
    doc = _doc()
    for token in ("${{Postgres.DATABASE_URL}}", "app_backend", "server_version_num", "rolsuper"):
        assert token in doc, token
    assert "alter role app_backend login password" not in doc.lower()


def test_deployment_doc_r2_setup() -> None:
    doc = _doc()
    for token in ("Object Read & Write", "r2.cloudflarestorage.com", "R2_BUCKET", "不需設定 CORS"):
        assert token in doc, token


def test_deployment_doc_line_setup() -> None:
    doc = _doc()
    for token in (
        "LINE Login channel",
        "LIFF",
        "/parent/",
        "bot_prompt",
        "aggressive",
        "Messaging API",
        "line.liff",
        "line.messaging",
        "channel access token",
        "channel secret",
    ):
        assert token in doc, token
    assert "bot link" in doc or "Linked LINE Official Account" in doc


def test_deployment_doc_line_values_not_env() -> None:
    assert not [
        key for key in _env_keys(API_ENV.read_text(encoding="utf-8")) if key.startswith("LINE_")
    ]
    assert not re.findall(r"\bLINE_[A-Z0-9_]+", env_section(_doc()))
    # 檢查函式本身會抓到違規
    assert re.findall(
        r"\bLINE_[A-Z0-9_]+", env_section("## 2. 環境變數\n| api | LINE_CHANNEL_SECRET |\n## 3. x")
    )


def test_deployment_doc_no_supabase() -> None:
    doc = _doc()
    assert "supabase" not in doc.lower()
    assert "db push" not in doc


def test_deployment_doc_config_file_absolute_paths() -> None:
    doc = _doc()
    assert "/apps/api/railway.json" in doc
    assert "/apps/web/railway.json" in doc
