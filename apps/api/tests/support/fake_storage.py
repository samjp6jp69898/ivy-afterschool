"""FakeStorage 測試替身（BACKEND-013）。

與 ``app.core.storage.Storage`` 結構相容；app 層測試以
``app.dependency_overrides[get_storage] = lambda: fake`` 注入
（BACKEND-023 的 fake_storage fixture）。

- ``create_signed_url`` 固定回 ``https://storage.test/{bucket}/{path}?exp={expires_in}``，
  下游 task 的 red_case 依賴這個格式，不得改變。
- path 與 R2Storage 走同一個 ``object_key`` 檢查。
- 失敗注入：把 ``upload_error`` / ``sign_error`` / ``delete_error`` 設成 StorageError，
  對應方法就拋出它且不改動 ``objects``。
"""

from collections.abc import Sequence

from app.core.storage import Bucket, StorageError, object_key


class FakeStorage:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.content_types: dict[tuple[str, str], str] = {}
        self.upload_error: StorageError | None = None
        self.sign_error: StorageError | None = None
        self.delete_error: StorageError | None = None

    def upload(self, bucket: Bucket, path: str, content: bytes, content_type: str) -> None:
        object_key(bucket, path)
        if self.upload_error is not None:
            raise self.upload_error
        self.objects[(bucket, path)] = content
        self.content_types[(bucket, path)] = content_type

    def create_signed_url(self, bucket: Bucket, path: str, expires_in: int = 300) -> str:
        object_key(bucket, path)
        if self.sign_error is not None:
            raise self.sign_error
        return f"https://storage.test/{bucket}/{path}?exp={expires_in}"

    def delete(self, bucket: Bucket, paths: Sequence[str]) -> None:
        for path in paths:
            object_key(bucket, path)
        if self.delete_error is not None:
            raise self.delete_error
        for path in paths:
            self.objects.pop((bucket, path), None)
            self.content_types.pop((bucket, path), None)
