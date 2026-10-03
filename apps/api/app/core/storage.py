"""BACKEND-013：以 boto3 的 S3 client 操作 Cloudflare R2（architecture_decisions §10）。

- 單一私有 bucket，以 key 前綴（邏輯分區）區分用途：``<bucket>/<owner_id>/<32 hex>.<ext>``。
  DB 欄位只存前綴之後的 path，不使用使用者提供的檔名。本機為 compose 的 SeaweedFS。
- 大小與格式不在這裡檢查：呼叫端一律先經 BACKEND-016 ``read_validated_upload`` 取得 bytes。
- 上游錯誤一律經 ``_s3_call`` 轉 StorageError，訊息只含 S3 錯誤碼與 HTTP status；在 ``except``
  之外才 raise，``__cause__`` 與 ``__context__`` 皆為 None——SignatureDoesNotMatch 等錯誤回應本文
  會帶 AWSAccessKeyId / StringToSign，不可隨例外鏈進 log。
- ``client`` 內含憑證：不得 log 或序列化。
- ``create_signed_url``（BACKEND-014）：SigV4 在本地簽章，不發網路請求、不檢查物件是否存在。
- ``delete``（BACKEND-015）：每批最多 1000 個 key；整個請求失敗立即拋出，個別 key 失敗則送完
  後續批次再彙整拋出（盡量多刪）。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from functools import lru_cache, partial
from typing import TYPE_CHECKING, Final, Literal, Protocol, get_args, runtime_checkable
from uuid import UUID, uuid4

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import get_settings

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

Bucket = Literal["leave-attachments", "student-photos", "pickup-person-photos"]

_BUCKETS: Final[frozenset[str]] = frozenset(get_args(Bucket))
_EXTENSIONS: Final = frozenset({"jpg", "png", "webp", "heic", "pdf"})
DELETE_BATCH_SIZE: Final = 1000
_DELETE_ERRORS_SHOWN: Final = 10
SIGNED_URL_MIN_SECONDS: Final = 30
SIGNED_URL_MAX_SECONDS: Final = 3600
_PATH = re.compile(r"[0-9a-f-]{36}/[0-9a-f]{32}\.(jpg|png|webp|heic|pdf)")


class StorageError(Exception):
    """上游錯誤 / 連線失敗；訊息不含 access key 與 secret。"""


@runtime_checkable
class Storage(Protocol):
    def upload(self, bucket: Bucket, path: str, content: bytes, content_type: str) -> None: ...

    def create_signed_url(self, bucket: Bucket, path: str, expires_in: int = 300) -> str: ...

    def delete(self, bucket: Bucket, paths: Sequence[str]) -> None: ...


def object_key(bucket: Bucket, path: str) -> str:
    if bucket not in _BUCKETS:
        raise ValueError(f"未知的 bucket 分區：{bucket!r}")
    if not _PATH.fullmatch(path):
        raise ValueError("storage path 格式不符（<owner_id>/<32 hex>.<ext>）")
    return f"{bucket}/{path}"


def build_object_path(owner_id: UUID, ext: str) -> str:
    if ext not in _EXTENSIONS:
        raise ValueError(f"不支援的 ext：{ext!r}")
    return f"{owner_id}/{uuid4().hex}.{ext}"


def _error_message(operation: str, exc: Exception) -> str:
    if isinstance(exc, ClientError):
        code = exc.response.get("Error", {}).get("Code", "?")
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", "?")
        return f"S3 {operation} 失敗：{code}（HTTP {status}）"
    return f"S3 {operation} 失敗：{type(exc).__name__}"


def _s3_call[T](operation: str, call: Callable[[], T]) -> T:
    """執行一次 S3 呼叫；上游錯誤只擷取錯誤碼與 status，離開 except 後才 raise StorageError。"""
    message: str
    try:
        return call()
    except (ClientError, BotoCoreError) as exc:
        message = _error_message(operation, exc)
    raise StorageError(message)


class R2Storage:
    def __init__(
        self,
        *,
        endpoint_url: str,
        access_key_id: str,
        secret_access_key: str,
        bucket_name: str,
        client: S3Client | None = None,
    ) -> None:
        # secret 只交給 boto3，不留在本物件的屬性上
        self.client: S3Client = client or boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            region_name="auto",
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                retries={"max_attempts": 3, "mode": "standard"},
                connect_timeout=5,
                read_timeout=10,
            ),
        )
        self.bucket_name = bucket_name

    def __repr__(self) -> str:
        return f"R2Storage(endpoint={self.client.meta.endpoint_url!r}, bucket={self.bucket_name!r})"

    def upload(self, bucket: Bucket, path: str, content: bytes, content_type: str) -> None:
        key = object_key(bucket, path)
        _s3_call(
            "put_object",
            lambda: self.client.put_object(
                Bucket=self.bucket_name, Key=key, Body=content, ContentType=content_type
            ),
        )

    def create_signed_url(self, bucket: Bucket, path: str, expires_in: int = 300) -> str:
        if not SIGNED_URL_MIN_SECONDS <= expires_in <= SIGNED_URL_MAX_SECONDS:
            raise ValueError(
                f"expires_in 必須介於 {SIGNED_URL_MIN_SECONDS}~{SIGNED_URL_MAX_SECONDS} 秒"
            )
        key = object_key(bucket, path)
        return _s3_call(
            "generate_presigned_url",
            lambda: self.client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket_name, "Key": key},
                ExpiresIn=expires_in,
            ),
        )

    def delete(self, bucket: Bucket, paths: Sequence[str]) -> None:
        # 全部先過格式檢查：任一不合法就一個請求都不送
        keys = [object_key(bucket, path) for path in paths]
        failures: list[str] = []
        for start in range(0, len(keys), DELETE_BATCH_SIZE):
            batch = keys[start : start + DELETE_BATCH_SIZE]
            response = _s3_call(
                "delete_objects",
                partial(
                    self.client.delete_objects,
                    Bucket=self.bucket_name,
                    Delete={"Objects": [{"Key": key} for key in batch], "Quiet": True},
                ),
            )
            failures += [
                f"{err.get('Key', '?')}（{err.get('Code', '?')}）"
                for err in response.get("Errors", [])
            ]
        if failures:
            shown = "、".join(failures[:_DELETE_ERRORS_SHOWN])
            raise StorageError(f"S3 delete_objects 有 {len(failures)} 個物件刪除失敗：{shown}")


@lru_cache(maxsize=1)
def get_storage() -> Storage:
    """FastAPI dependency：以 Settings 的 r2_* 建立的單例。

    測試以 ``app.dependency_overrides[get_storage]`` 換成 FakeStorage。
    """
    settings = get_settings()
    return R2Storage(
        endpoint_url=str(settings.r2_endpoint_url).rstrip("/"),
        access_key_id=settings.r2_access_key_id,
        secret_access_key=settings.r2_secret_access_key.get_secret_value(),
        bucket_name=settings.r2_bucket,
    )
