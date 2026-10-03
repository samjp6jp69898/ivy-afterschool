"""BACKEND-013：R2Storage.upload、object_key、build_object_path、get_storage 與 FakeStorage。

S3 呼叫一律以 botocore.stub.Stubber 攔截，不連網路。
"""

import re
import traceback
from collections.abc import Iterator
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse
from uuid import UUID, uuid4

import boto3
import pytest
from botocore.exceptions import EndpointConnectionError, NoCredentialsError
from botocore.stub import Stubber
from mypy_boto3_s3 import S3Client

from app.core.config import get_settings
from app.core.storage import (
    R2Storage,
    Storage,
    StorageError,
    build_object_path,
    get_storage,
    object_key,
)
from tests.support.fake_storage import FakeStorage

_ACCESS_KEY = "AKIDTESTONLY0000"
_SECRET_VALUE = "z" * 40  # 假的 R2 secret，只用來確認不會出現在錯誤訊息
_BUCKET_NAME = "afterschool-test"
_OWNER = UUID("11111111-1111-1111-1111-111111111111")


def _valid_path(ext: str = "jpg") -> str:
    return f"{_OWNER}/{uuid4().hex}.{ext}"


@pytest.fixture
def s3_client() -> S3Client:
    return boto3.client(
        "s3",
        endpoint_url="http://127.0.0.1:54344",
        region_name="auto",
        aws_access_key_id=_ACCESS_KEY,
        aws_secret_access_key=_SECRET_VALUE,
    )


@pytest.fixture
def storage(s3_client: S3Client) -> R2Storage:
    return R2Storage(
        endpoint_url="http://127.0.0.1:54344",
        access_key_id=_ACCESS_KEY,
        secret_access_key=_SECRET_VALUE,
        bucket_name=_BUCKET_NAME,
        client=s3_client,
    )


@pytest.fixture
def stubber(storage: R2Storage) -> Iterator[Stubber]:
    with Stubber(storage.client) as stub:
        yield stub
        # 登記了卻沒被呼叫的 response 也算失敗
        stub.assert_no_pending_responses()


# --- upload -------------------------------------------------------------------------------


def test_storage_upload_request(storage: R2Storage, stubber: Stubber) -> None:
    path = _valid_path("jpg")
    stubber.add_response(
        "put_object",
        {},
        {
            "Bucket": _BUCKET_NAME,
            "Key": f"student-photos/{path}",
            "Body": b"\xff\xd8",
            "ContentType": "image/jpeg",
        },
    )

    storage.upload("student-photos", path, b"\xff\xd8", "image/jpeg")


@pytest.mark.parametrize("bucket", ["leave-attachments", "student-photos", "pickup-person-photos"])
def test_storage_upload_key_prefix_per_bucket(
    storage: R2Storage, stubber: Stubber, bucket: str
) -> None:
    path = _valid_path("pdf")
    stubber.add_response(
        "put_object",
        {},
        {
            "Bucket": _BUCKET_NAME,
            "Key": f"{bucket}/{path}",
            "Body": b"%PDF-",
            "ContentType": "application/pdf",
        },
    )

    storage.upload(bucket, path, b"%PDF-", "application/pdf")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "path",
    [
        "../etc/passwd",
        "abc/photo.jpg",
        f"{_OWNER}/{'a' * 32}.exe",
        f"{_OWNER}/{'a' * 32}.JPG",
        f"{_OWNER}/{'A' * 32}.jpg",
        f"{_OWNER}/{'a' * 31}.jpg",
        f"{_OWNER}/../{'a' * 32}.jpg",
        f"/{_OWNER}/{'a' * 32}.jpg",
        f"student-photos/{_OWNER}/{'a' * 32}.jpg",
        f"{_OWNER}/{'a' * 32}.jpg\n",
        f"{_OWNER}/{'a' * 32}.jpg/x",
        "",
    ],
)
def test_storage_upload_rejects_bad_path(storage: R2Storage, stubber: Stubber, path: str) -> None:
    # Stubber 未登記任何 response：若有 S3 呼叫會拋 StubResponseError
    with pytest.raises(ValueError, match="path"):
        storage.upload("student-photos", path, b"\xff\xd8", "image/jpeg")


def test_storage_upload_upstream_error(storage: R2Storage, stubber: Stubber) -> None:
    stubber.add_client_error("put_object", service_error_code="InternalError", http_status_code=500)

    with pytest.raises(StorageError) as exc_info:
        storage.upload("student-photos", _valid_path(), b"\xff\xd8", "image/jpeg")

    message = str(exc_info.value)
    assert "500" in message
    assert "InternalError" in message
    assert _SECRET_VALUE not in message


def test_storage_upload_error_hides_credentials(storage: R2Storage, stubber: Stubber) -> None:
    # S3 的 SignatureDoesNotMatch 回應本文會帶 AWSAccessKeyId / StringToSign
    stubber.add_client_error(
        "put_object",
        service_error_code="SignatureDoesNotMatch",
        service_message=f"Signature does not match. AWSAccessKeyId={_ACCESS_KEY}",
        http_status_code=403,
        response_meta={"AWSAccessKeyId": _ACCESS_KEY, "StringToSign": "PUT\n..."},
    )

    with pytest.raises(StorageError) as exc_info:
        storage.upload("leave-attachments", _valid_path("pdf"), b"%PDF-", "application/pdf")

    exc = exc_info.value
    assert "SignatureDoesNotMatch" in str(exc)
    assert "403" in str(exc)
    assert _ACCESS_KEY not in str(exc)
    assert _SECRET_VALUE not in str(exc)
    assert exc.__cause__ is None
    assert exc.__context__ is None


def test_storage_upload_connection_error(
    storage: R2Storage, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(**kwargs: object) -> None:
        raise EndpointConnectionError(endpoint_url="http://127.0.0.1:54344")

    monkeypatch.setattr(storage.client, "put_object", refuse)

    with pytest.raises(StorageError) as exc_info:
        storage.upload("student-photos", _valid_path(), b"\xff\xd8", "image/jpeg")

    assert "EndpointConnectionError" in str(exc_info.value)
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None


def test_storage_error_has_no_context(storage: R2Storage, stubber: Stubber) -> None:
    stubber.add_client_error(
        "put_object",
        service_error_code="SignatureDoesNotMatch",
        http_status_code=403,
        service_message="AWSAccessKeyId AKIDLEAK StringToSign STSLEAK",
    )

    with pytest.raises(StorageError) as exc_info:
        storage.upload("student-photos", _valid_path(), b"\xff\xd8", "image/jpeg")

    err = exc_info.value
    assert err.__cause__ is None
    assert err.__context__ is None
    rendered = "".join(traceback.format_exception(err))
    assert "AKIDLEAK" not in rendered
    assert "STSLEAK" not in rendered
    assert "SignatureDoesNotMatch" in rendered


# --- create_signed_url（BACKEND-014） ------------------------------------------------------


def _cloud_storage() -> R2Storage:
    return R2Storage(
        endpoint_url="https://acct.r2.cloudflarestorage.com",
        access_key_id=_ACCESS_KEY,
        secret_access_key=_SECRET_VALUE,
        bucket_name=_BUCKET_NAME,
    )


def test_signed_url_structure() -> None:
    path = _valid_path("jpg")

    url = _cloud_storage().create_signed_url("student-photos", path, 300)

    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    assert parsed.scheme == "https"
    assert parsed.netloc == "acct.r2.cloudflarestorage.com"
    assert parsed.path == f"/{_BUCKET_NAME}/student-photos/{path}"
    assert query["X-Amz-Expires"] == ["300"]
    assert query["X-Amz-Algorithm"] == ["AWS4-HMAC-SHA256"]
    assert re.fullmatch(r"[0-9a-f]{64}", query["X-Amz-Signature"][0])
    assert query["X-Amz-Credential"][0].startswith(f"{_ACCESS_KEY}/")
    assert "/auto/s3/aws4_request" in query["X-Amz-Credential"][0]


def test_signed_url_default_expiry_and_bucket_prefix() -> None:
    path = _valid_path("pdf")

    url = _cloud_storage().create_signed_url("leave-attachments", path)

    parsed = urlparse(url)
    assert parsed.path == f"/{_BUCKET_NAME}/leave-attachments/{path}"
    assert parse_qs(parsed.query)["X-Amz-Expires"] == ["300"]


def test_signed_url_expires_range() -> None:
    storage = _cloud_storage()
    path = _valid_path()

    for bad in (10, 29, 3601, 0, -1):
        with pytest.raises(ValueError, match="expires_in"):
            storage.create_signed_url("student-photos", path, bad)
    for ok in (30, 3600):
        url = storage.create_signed_url("student-photos", path, ok)
        assert parse_qs(urlparse(url).query)["X-Amz-Expires"] == [str(ok)]


def test_signed_url_offline(storage: R2Storage, stubber: Stubber) -> None:
    # Stubber 啟用但未登記任何 response：有任何 S3 API 呼叫都會拋錯
    path = _valid_path("png")

    url = storage.create_signed_url("pickup-person-photos", path)

    assert urlparse(url).path == f"/{_BUCKET_NAME}/pickup-person-photos/{path}"
    with pytest.raises(ValueError, match="path"):
        storage.create_signed_url("pickup-person-photos", "x/y.jpg")


def test_signed_url_does_not_leak_secret() -> None:
    url = _cloud_storage().create_signed_url("student-photos", _valid_path())

    assert _SECRET_VALUE not in url
    assert _SECRET_VALUE not in unquote(url)


def test_signed_url_signing_error(storage: R2Storage, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: object, **kwargs: object) -> str:
        raise NoCredentialsError()

    monkeypatch.setattr(storage.client, "generate_presigned_url", fail)

    with pytest.raises(StorageError) as exc_info:
        storage.create_signed_url("student-photos", _valid_path())

    assert "NoCredentialsError" in str(exc_info.value)
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None


# --- object_key / build_object_path ---------------------------------------------------------


def test_storage_build_object_path() -> None:
    path = build_object_path(_OWNER, "png")
    assert re.fullmatch(r"11111111-1111-1111-1111-111111111111/[0-9a-f]{32}\.png", path)
    assert build_object_path(_OWNER, "png") != path

    for ext in ("jpg", "png", "webp", "heic", "pdf"):
        assert build_object_path(_OWNER, ext).endswith(f".{ext}")
    for bad in ("exe", "PNG", "jpeg", "", "xlsx", "pdf/../x"):
        with pytest.raises(ValueError, match="ext"):
            build_object_path(_OWNER, bad)

    assert object_key("leave-attachments", path) == "leave-attachments/" + path


def test_storage_object_key_rejects_unknown_bucket() -> None:
    with pytest.raises(ValueError, match="bucket"):
        object_key("other", _valid_path())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="path"):
        object_key("student-photos", "x.jpg")


# --- 預設 client 與 get_storage ---------------------------------------------------------------


def test_storage_default_client_config() -> None:
    storage = R2Storage(
        endpoint_url="http://127.0.0.1:54344",
        access_key_id=_ACCESS_KEY,
        secret_access_key=_SECRET_VALUE,
        bucket_name=_BUCKET_NAME,
    )

    meta = storage.client.meta
    # botocore 的 Config stubs 沒有宣告這些屬性
    config: Any = meta.config
    assert meta.endpoint_url == "http://127.0.0.1:54344"
    assert meta.region_name == "auto"
    assert config.signature_version == "s3v4"
    assert config.s3 == {"addressing_style": "path"}
    # botocore 把 max_attempts=3（重試次數）正規化為 total_max_attempts=4（含第一次）
    assert config.retries == {"mode": "standard", "total_max_attempts": 4}
    assert config.connect_timeout == 5
    assert config.read_timeout == 10
    assert storage.bucket_name == _BUCKET_NAME
    assert _SECRET_VALUE not in repr(storage)
    assert _SECRET_VALUE not in str(vars(storage))


@pytest.fixture
def r2_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@127.0.0.1:54342/postgres")
    monkeypatch.setenv("APP_SECRET_KEY", "s" * 48)
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://127.0.0.1:5341")
    monkeypatch.setenv("R2_ENDPOINT_URL", "http://127.0.0.1:54344")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", _ACCESS_KEY)
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", _SECRET_VALUE)
    monkeypatch.setenv("R2_BUCKET", _BUCKET_NAME)
    get_settings.cache_clear()
    get_storage.cache_clear()
    yield
    get_settings.cache_clear()
    get_storage.cache_clear()


@pytest.mark.usefixtures("r2_env")
def test_storage_get_storage_uses_settings() -> None:
    storage = get_storage()

    assert isinstance(storage, R2Storage)
    assert storage.client.meta.endpoint_url == "http://127.0.0.1:54344"
    assert storage.bucket_name == _BUCKET_NAME
    assert get_storage() is storage


# --- FakeStorage ----------------------------------------------------------------------------


def test_storage_fake_storage_protocol() -> None:
    fake = FakeStorage()
    path = _valid_path("pdf")

    fake.upload("leave-attachments", path, b"x", "application/pdf")

    assert fake.objects[("leave-attachments", path)] == b"x"
    assert isinstance(fake, Storage)
    storage: Storage = fake  # mypy：結構相容
    assert storage.create_signed_url("leave-attachments", path) == (
        f"https://storage.test/leave-attachments/{path}?exp=300"
    )


def test_storage_r2_storage_is_storage(storage: R2Storage) -> None:
    assert isinstance(storage, Storage)


def test_storage_fake_storage_failures() -> None:
    fake = FakeStorage()
    path = _valid_path("jpg")
    other = _valid_path("png")
    fake.upload("student-photos", path, b"a", "image/jpeg")

    assert fake.create_signed_url("student-photos", path, expires_in=60) == (
        f"https://storage.test/student-photos/{path}?exp=60"
    )

    fake.upload_error = StorageError("upload down")
    with pytest.raises(StorageError, match="upload down"):
        fake.upload("student-photos", other, b"b", "image/png")
    assert ("student-photos", other) not in fake.objects

    fake.sign_error = StorageError("sign down")
    with pytest.raises(StorageError, match="sign down"):
        fake.create_signed_url("student-photos", path)

    fake.delete_error = StorageError("delete down")
    with pytest.raises(StorageError, match="delete down"):
        fake.delete("student-photos", [path])
    assert fake.objects[("student-photos", path)] == b"a"

    fake.delete_error = None
    fake.delete("student-photos", [path, other])
    assert fake.objects == {}

    with pytest.raises(ValueError, match="path"):
        fake.upload("student-photos", "../x.jpg", b"x", "image/jpeg")
