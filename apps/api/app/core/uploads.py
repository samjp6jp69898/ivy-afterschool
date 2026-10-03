"""BACKEND-016：檔案上傳的大小上限與檔頭（magic bytes）檢查。

所有檔案上傳（學生照片、請假附件、接送人照片、Excel 匯入）先經過 ``read_validated_upload``。
移植 ivy ``utils/file_upload.py::read_upload_with_size_check`` / ``validate_file_signature``：
格式一律由檔頭判定，不信任 client 的 content_type 與副檔名。
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from typing import Final

from starlette.datastructures import UploadFile  # fastapi.UploadFile 的基底，兩者都能傳入

from app.core.errors import AppError

IMAGE_TYPES: Final = frozenset({"jpg", "png", "webp"})
ATTACHMENT_TYPES: Final = IMAGE_TYPES | {"heic", "pdf"}
XLSX_TYPES: Final = frozenset({"xlsx"})

_MIB: Final = 1024 * 1024
# 上限全部由應用層把關（R2 bucket 沒有逐分區的大小設定，architecture_decisions §10）
PHOTO_MAX_BYTES: Final = 5 * _MIB
ATTACHMENT_MAX_BYTES: Final = 10 * _MIB
XLSX_MAX_BYTES: Final = 5 * _MIB

_CHUNK_SIZE: Final = 64 * 1024

_MIME_TYPES: Final[dict[str, str]] = {
    "jpg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
    "heic": "image/heic",
    "pdf": "application/pdf",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
_HEIC_BRANDS: Final = frozenset({b"heic", b"heix", b"mif1"})


@dataclass(frozen=True)
class ValidatedUpload:
    content: bytes
    mime_type: str  # 由檔頭判定，不信任 client 的 content_type
    ext: str  # jpg / png / webp / heic / pdf / xlsx
    size: int


def _is_xlsx(content: bytes) -> bool:
    # 只讀 central directory，不解壓任何項目
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            names = zf.namelist()
    except Exception:  # 惡意構造的 zip 可能拋各種解析例外，一律視為非 xlsx（415 而非 500）
        return False
    return "[Content_Types].xml" in names and any(n.startswith("xl/") for n in names)


def _detect(content: bytes) -> str | None:
    if content.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "webp"
    if content[4:8] == b"ftyp" and content[8:12] in _HEIC_BRANDS:
        return "heic"
    if content.startswith(b"%PDF-"):
        return "pdf"
    if content.startswith(b"PK\x03\x04") and _is_xlsx(content):
        return "xlsx"
    return None


def _format_limit(max_bytes: int) -> str:
    if max_bytes >= _MIB:
        return f"{max_bytes / _MIB:g} MB"
    return f"{max_bytes / 1024:g} KB"


def read_validated_upload(
    file: UploadFile, *, allowed: frozenset[str], max_bytes: int
) -> ValidatedUpload:
    buf = bytearray()
    while chunk := file.file.read(_CHUNK_SIZE):
        buf += chunk
        if len(buf) > max_bytes:
            raise AppError(
                "file_too_large",
                f"檔案超過 {_format_limit(max_bytes)}",
                status=413,
                details={"max_bytes": max_bytes},
            )
    if not buf:
        raise AppError("file_empty", "檔案是空的", status=422)

    content = bytes(buf)
    ext = _detect(content)
    if ext is None or ext not in allowed:
        raise AppError(
            "unsupported_file_type",
            "不支援的檔案格式",
            status=415,
            details={"allowed": sorted(allowed)},
        )
    return ValidatedUpload(content=content, mime_type=_MIME_TYPES[ext], ext=ext, size=len(content))
