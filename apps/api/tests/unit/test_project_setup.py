"""INFRA-004：apps/api 專案設定（Python 版本、依賴、pytest-socket、ruff 禁用 API）。"""

import importlib
import socket
import subprocess
import sys
from pathlib import Path

import pytest
import pytest_socket

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"

RUNTIME_MODULES = [
    "fastapi",
    "uvicorn",
    "sqlalchemy",
    "psycopg",
    "pydantic",
    "pydantic_settings",
    "argon2",
    "cryptography",
    "jwt",
    "httpx",
    "apscheduler",
    "openpyxl",
    "sentry_sdk",
    # python-multipart 的模組名（舊名 multipart 會發 PendingDeprecationWarning）
    "python_multipart",
]


def test_project_setup_python_is_3_13() -> None:
    assert sys.version_info[:2] == (3, 13)


def test_project_setup_runtime_dependencies_importable() -> None:
    imported = [importlib.import_module(name).__name__ for name in RUNTIME_MODULES]

    assert imported == RUNTIME_MODULES
    apscheduler = importlib.import_module("apscheduler")
    assert apscheduler.__version__.startswith("3.")


def test_project_setup_sqlalchemy_is_v2() -> None:
    sqlalchemy = importlib.import_module("sqlalchemy")

    assert sqlalchemy.__version__.startswith("2.")


def test_project_setup_blocks_external_socket() -> None:
    # 全域 --allow-hosts 下 pytest-socket 對非允許 host 拋 SocketConnectBlockedError；
    # 整個 socket 被停用時拋 SocketBlockedError（兩者互不為子類別）。
    with pytest.raises((pytest_socket.SocketBlockedError, pytest_socket.SocketConnectBlockedError)):
        socket.create_connection(("1.1.1.1", 53), timeout=1)


def test_project_setup_ruff_bans_datetime_now(tmp_path: Path) -> None:
    probe = tmp_path / "probe.py"
    probe.write_text("import datetime\n\nNOW = datetime.datetime.now()\n")

    result = subprocess.run(  # noqa: S603  固定參數的 ruff 呼叫
        [sys.executable, "-m", "ruff", "check", "--config", str(PYPROJECT), str(probe)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "TID251" in result.stdout
