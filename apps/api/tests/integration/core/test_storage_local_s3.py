"""BACKEND-536：R2Storage 對本機 SeaweedFS S3 的往返（上傳、presigned 下載、刪除）。

補足 Stubber 單元測試看不到的 SigV4 簽章與 path-style 行為。每個測試以新的
``build_object_path(uuid4(), ...)`` 建物件，結束時刪除自己建立的物件。
"""

from collections.abc import Iterator
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit
from uuid import uuid4

import httpx
import pytest

from app.core.storage import Bucket, R2Storage, StorageError, build_object_path
from tests.support.local_s3 import (
    DEFAULT_SECRET_ACCESS_KEY,
    local_s3_bucket,
    local_s3_endpoint,
    local_s3_storage,
)

__all__ = ["local_s3_storage"]  # fixture 以 import 註冊

_JPEG = b"\xff\xd8\xffs3-probe"


@pytest.fixture
def created(local_s3_storage: R2Storage) -> Iterator[list[tuple[Bucket, str]]]:
    """測試建立的物件；結束時一律刪除。"""
    objects: list[tuple[Bucket, str]] = []
    yield objects
    for bucket, path in objects:
        local_s3_storage.delete(bucket, [path])


def _upload(
    storage: R2Storage,
    created: list[tuple[Bucket, str]],
    bucket: Bucket = "student-photos",
    ext: str = "jpg",
    content: bytes = _JPEG,
    content_type: str = "image/jpeg",
) -> str:
    path = build_object_path(uuid4(), ext)
    storage.upload(bucket, path, content, content_type)
    created.append((bucket, path))
    return path


def test_storage_local_s3_round_trip(
    local_s3_storage: R2Storage, created: list[tuple[Bucket, str]]
) -> None:
    path = _upload(local_s3_storage, created)

    response = httpx.get(local_s3_storage.create_signed_url("student-photos", path, 60))

    assert response.status_code == 200
    assert response.content == _JPEG
    assert response.headers["content-type"] == "image/jpeg"


@pytest.mark.parametrize(
    ("bucket", "ext", "content_type"),
    [
        ("leave-attachments", "pdf", "application/pdf"),
        ("pickup-person-photos", "png", "image/png"),
    ],
)
def test_storage_local_s3_round_trip_other_prefixes(
    local_s3_storage: R2Storage,
    created: list[tuple[Bucket, str]],
    bucket: Bucket,
    ext: str,
    content_type: str,
) -> None:
    content = f"{bucket}-{uuid4().hex}".encode()
    path = _upload(local_s3_storage, created, bucket, ext, content, content_type)

    url = local_s3_storage.create_signed_url(bucket, path)
    response = httpx.get(url)

    assert urlsplit(url).path == f"/{local_s3_bucket()}/{bucket}/{path}"
    assert response.status_code == 200
    assert response.content == content
    assert response.headers["content-type"] == content_type


def test_storage_local_s3_unsigned_access_denied(
    local_s3_storage: R2Storage, created: list[tuple[Bucket, str]]
) -> None:
    path = _upload(local_s3_storage, created)

    response = httpx.get(f"{local_s3_endpoint()}/{local_s3_bucket()}/student-photos/{path}")

    assert response.status_code == 403
    assert _JPEG not in response.content


def test_storage_local_s3_tampered_signature_rejected(
    local_s3_storage: R2Storage, created: list[tuple[Bucket, str]]
) -> None:
    path = _upload(local_s3_storage, created)
    url = local_s3_storage.create_signed_url("student-photos", path, 60)
    parts = urlsplit(url)
    query = {k: v[0] for k, v in parse_qs(parts.query).items()}
    signature = query["X-Amz-Signature"]
    query["X-Amz-Signature"] = signature[:-1] + ("0" if signature[-1] != "0" else "1")

    response = httpx.get(urlunsplit(parts._replace(query=urlencode(query))))

    assert response.status_code == 403
    assert _JPEG not in response.content


def test_storage_local_s3_delete(
    local_s3_storage: R2Storage, created: list[tuple[Bucket, str]]
) -> None:
    path = _upload(local_s3_storage, created)

    local_s3_storage.delete("student-photos", [path])

    response = httpx.get(local_s3_storage.create_signed_url("student-photos", path, 60))
    assert response.status_code == 404
    # 刪除不存在的物件視為成功
    local_s3_storage.delete("student-photos", [path])


def test_storage_local_s3_delete_batch_mixed(
    local_s3_storage: R2Storage, created: list[tuple[Bucket, str]]
) -> None:
    kept = _upload(local_s3_storage, created)
    first = _upload(local_s3_storage, created)
    second = _upload(local_s3_storage, created)
    never_uploaded = build_object_path(uuid4(), "jpg")

    local_s3_storage.delete("student-photos", [first, never_uploaded, second])

    for path, expected in ((first, 404), (second, 404), (kept, 200)):
        url = local_s3_storage.create_signed_url("student-photos", path, 60)
        assert httpx.get(url).status_code == expected, path


def test_storage_local_s3_bad_credentials_upload_fails() -> None:
    wrong_secret = "w" * 40
    storage = R2Storage(
        endpoint_url=local_s3_endpoint(),
        access_key_id="afterschool",
        secret_access_key=wrong_secret,
        bucket_name=local_s3_bucket(),
    )
    path = build_object_path(uuid4(), "jpg")

    with pytest.raises(StorageError) as exc_info:
        storage.upload("student-photos", path, _JPEG, "image/jpeg")

    assert "403" in str(exc_info.value)
    assert wrong_secret not in str(exc_info.value)
    assert DEFAULT_SECRET_ACCESS_KEY not in str(exc_info.value)
    assert exc_info.value.__context__ is None


_PYTESTER_TEST = """
from tests.support.local_s3 import local_s3_storage


def test_uses_storage(local_s3_storage):
    raise AssertionError("不應執行到測試本體")
"""


def test_storage_local_s3_rejects_remote_endpoint(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TEST_R2_ENDPOINT_URL", "https://acct.r2.cloudflarestorage.com")
    created_clients: list[object] = []

    def record_client(*args: object, **kwargs: object) -> None:
        created_clients.append(args)

    monkeypatch.setattr("boto3.client", record_client)
    pytester.makepyfile(_PYTESTER_TEST)

    result = pytester.runpytest("-p", "no:cacheprovider")

    assert result.ret == 2
    result.stdout.fnmatch_lines(["*只允許本機 loopback 儲存服務*"])
    assert created_clients == []


def test_storage_local_s3_exits_when_not_listening(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    # port 1 在本機不會有服務監聽
    monkeypatch.setenv("TEST_R2_ENDPOINT_URL", "http://127.0.0.1:1")
    pytester.makepyfile(_PYTESTER_TEST)

    result = pytester.runpytest("-p", "no:cacheprovider")

    assert result.ret == 2
    result.stdout.fnmatch_lines(["*本機 S3（SeaweedFS）未啟動，先跑 just db-start*"])
