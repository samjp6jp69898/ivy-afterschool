"""本機 S3（compose 的 SeaweedFS）整合測試 fixture（BACKEND-536）。

- 連線設定讀 ``TEST_R2_*``，預設值與 apps/api/.env.example 一致。
- endpoint 的 host 不是 loopback → ``pytest.exit``，且不建立 boto3 client。
- TCP 探測連不上 → ``pytest.exit``（提示 just db-start）。
- 使用端在 test 模組 ``from tests.support.local_s3 import local_s3_storage`` 即可取得 fixture。
"""

import os
import socket
from urllib.parse import urlsplit

import pytest

from app.core.storage import R2Storage

DEFAULT_ENDPOINT = "http://127.0.0.1:54344"
DEFAULT_ACCESS_KEY_ID = "afterschool"
DEFAULT_SECRET_ACCESS_KEY = "afterschool-local-secret"  # noqa: S105  本機 SeaweedFS 固定開發值
DEFAULT_BUCKET = "afterschool-local"

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_EXIT_CODE = 2


def local_s3_endpoint() -> str:
    return os.environ.get("TEST_R2_ENDPOINT_URL", DEFAULT_ENDPOINT)


def local_s3_bucket() -> str:
    return os.environ.get("TEST_R2_BUCKET", DEFAULT_BUCKET)


def _require_loopback(endpoint: str) -> tuple[str, int]:
    parts = urlsplit(endpoint)
    host = parts.hostname or ""
    if host not in _LOOPBACK_HOSTS:
        pytest.exit(f"只允許本機 loopback 儲存服務：{host or endpoint}", returncode=_EXIT_CODE)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    return host, port


def _require_listening(host: str, port: int) -> None:
    try:
        with socket.create_connection((host, port), timeout=1):
            pass
    except OSError:
        pytest.exit("本機 S3（SeaweedFS）未啟動，先跑 just db-start", returncode=_EXIT_CODE)


@pytest.fixture
def local_s3_storage() -> R2Storage:
    endpoint = local_s3_endpoint()
    host, port = _require_loopback(endpoint)
    _require_listening(host, port)
    return R2Storage(
        endpoint_url=endpoint,
        access_key_id=os.environ.get("TEST_R2_ACCESS_KEY_ID", DEFAULT_ACCESS_KEY_ID),
        secret_access_key=os.environ.get("TEST_R2_SECRET_ACCESS_KEY", DEFAULT_SECRET_ACCESS_KEY),
        bucket_name=local_s3_bucket(),
    )
