"""BACKEND-016：read_validated_upload（大小上限與檔頭 magic bytes 檢查）。"""

import io
import tempfile
import zipfile

import pytest
from openpyxl import Workbook
from starlette.datastructures import Headers, UploadFile

from app.core.errors import AppError
from app.core.uploads import (
    ATTACHMENT_MAX_BYTES,
    ATTACHMENT_TYPES,
    IMAGE_TYPES,
    PHOTO_MAX_BYTES,
    XLSX_MAX_BYTES,
    XLSX_TYPES,
    ValidatedUpload,
    read_validated_upload,
)

_JPEG = b"\xff\xd8\xff\xe0" + b"0" * 100
_PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 50
_PDF = b"%PDF-1.7\n" + b"0" * 50
_WEBP = b"RIFF" + b"\x24\x00\x00\x00" + b"WEBPVP8 " + b"0" * 40
_MB = 1024 * 1024
_ALL = ATTACHMENT_TYPES | XLSX_TYPES


def _upload(content: bytes, filename: str = "a.bin", content_type: str | None = None) -> UploadFile:
    headers = Headers({"content-type": content_type}) if content_type else None
    return UploadFile(file=io.BytesIO(content), filename=filename, headers=headers)


def _heic(brand: bytes) -> bytes:
    return b"\x00\x00\x00\x18" + b"ftyp" + brand + b"\x00\x00\x00\x00mif1heic" + b"0" * 20


def _xlsx_bytes() -> bytes:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws["A1"] = "王小明"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _zip_bytes(names: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in names.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _app_error(exc_info: pytest.ExceptionInfo[AppError]) -> tuple[str, int]:
    return exc_info.value.code, exc_info.value.status


def test_uploads_detect_jpeg_png_pdf() -> None:
    jpg = read_validated_upload(_upload(_JPEG, "a.jpg"), allowed=IMAGE_TYPES, max_bytes=_MB)
    assert jpg == ValidatedUpload(content=_JPEG, mime_type="image/jpeg", ext="jpg", size=104)

    png = read_validated_upload(_upload(_PNG, "a.png"), allowed=IMAGE_TYPES, max_bytes=_MB)
    assert (png.ext, png.mime_type, png.size) == ("png", "image/png", len(_PNG))

    pdf = read_validated_upload(_upload(_PDF, "a.pdf"), allowed=ATTACHMENT_TYPES, max_bytes=_MB)
    assert (pdf.ext, pdf.mime_type) == ("pdf", "application/pdf")
    assert pdf.content == _PDF


def test_uploads_detect_webp_heic() -> None:
    webp = read_validated_upload(_upload(_WEBP), allowed=IMAGE_TYPES, max_bytes=_MB)
    assert (webp.ext, webp.mime_type) == ("webp", "image/webp")

    for brand in (b"heic", b"heix", b"mif1"):
        heic = read_validated_upload(_upload(_heic(brand)), allowed=ATTACHMENT_TYPES, max_bytes=_MB)
        assert (heic.ext, heic.mime_type) == ("heic", "image/heic")


@pytest.mark.parametrize(
    "content",
    [
        b"RIFF\x24\x00\x00\x00WAVEfmt " + b"0" * 20,  # RIFF 但不是 WEBP
        _heic(b"avif"),  # ftyp 但品牌不是 HEIC
        b"\x00\x00\x00\x18ftypmp42" + b"0" * 20,  # mp4
        b"GIF89a" + b"0" * 20,
        b"\xff\xd8",  # JPEG 檔頭不完整
        b"%PDF",  # 缺 '-'
        b"hello world",
    ],
)
def test_uploads_unknown_signature_rejected(content: bytes) -> None:
    with pytest.raises(AppError) as exc_info:
        read_validated_upload(_upload(content), allowed=_ALL, max_bytes=_MB)

    assert _app_error(exc_info) == ("unsupported_file_type", 415)


def test_uploads_content_over_extension() -> None:
    with pytest.raises(AppError) as exc_info:
        read_validated_upload(
            _upload(b"%PDF-1.4", "photo.jpg", "image/jpeg"), allowed=IMAGE_TYPES, max_bytes=_MB
        )

    assert _app_error(exc_info) == ("unsupported_file_type", 415)


def test_uploads_ignores_client_content_type() -> None:
    result = read_validated_upload(
        _upload(_JPEG, "photo.png", "image/png"), allowed=IMAGE_TYPES, max_bytes=_MB
    )

    assert (result.ext, result.mime_type) == ("jpg", "image/jpeg")


def test_uploads_too_large() -> None:
    content = b"\xff\xd8\xff\xe0" + b"0" * 2000

    with pytest.raises(AppError) as exc_info:
        read_validated_upload(_upload(content), allowed=IMAGE_TYPES, max_bytes=1024)

    assert _app_error(exc_info) == ("file_too_large", 413)


def test_uploads_size_boundary() -> None:
    exact = b"\xff\xd8\xff\xe0" + b"0" * 1020
    assert len(exact) == 1024
    assert read_validated_upload(_upload(exact), allowed=IMAGE_TYPES, max_bytes=1024).size == 1024

    with pytest.raises(AppError) as exc_info:
        read_validated_upload(_upload(exact + b"0"), allowed=IMAGE_TYPES, max_bytes=1024)
    assert _app_error(exc_info) == ("file_too_large", 413)


def test_uploads_too_large_message_in_mb() -> None:
    content = b"\xff\xd8\xff\xe0" + b"0" * (5 * _MB)

    with pytest.raises(AppError) as exc_info:
        read_validated_upload(_upload(content), allowed=IMAGE_TYPES, max_bytes=PHOTO_MAX_BYTES)

    assert exc_info.value.message == "檔案超過 5 MB"


class _CountingReader(io.RawIOBase):
    """無限長的 JPEG 串流，記錄實際讀了多少 bytes。"""

    def __init__(self) -> None:
        self.read_total = 0
        self._header_sent = False

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: memoryview) -> int:  # type: ignore[override]
        n = len(buffer)
        if not self._header_sent:
            buffer[:4] = b"\xff\xd8\xff\xe0"
            buffer[4:n] = b"0" * (n - 4)
            self._header_sent = True
        else:
            buffer[:n] = b"0" * n
        self.read_total += n
        return n


def test_uploads_stops_reading_once_over_limit() -> None:
    reader = _CountingReader()
    upload = UploadFile(file=io.BufferedReader(reader, buffer_size=1), filename="huge.jpg")

    with pytest.raises(AppError) as exc_info:
        read_validated_upload(upload, allowed=IMAGE_TYPES, max_bytes=100 * 1024)

    assert _app_error(exc_info) == ("file_too_large", 413)
    # 最多多讀一個 chunk（64 KiB）就停
    assert reader.read_total <= 100 * 1024 + 64 * 1024


def test_uploads_empty() -> None:
    with pytest.raises(AppError) as exc_info:
        read_validated_upload(_upload(b""), allowed=IMAGE_TYPES, max_bytes=_MB)

    assert _app_error(exc_info) == ("file_empty", 422)


def test_uploads_type_not_in_allowed() -> None:
    with pytest.raises(AppError) as exc_info:
        read_validated_upload(_upload(_PDF, "a.pdf"), allowed=IMAGE_TYPES, max_bytes=_MB)
    assert _app_error(exc_info) == ("unsupported_file_type", 415)

    with pytest.raises(AppError) as exc_info:
        read_validated_upload(_upload(_JPEG, "a.jpg"), allowed=XLSX_TYPES, max_bytes=_MB)
    assert _app_error(exc_info) == ("unsupported_file_type", 415)


def test_uploads_xlsx_detect() -> None:
    content = _xlsx_bytes()

    result = read_validated_upload(
        _upload(content, "students.xlsx"), allowed=XLSX_TYPES, max_bytes=_MB
    )

    assert result.ext == "xlsx"
    assert result.mime_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert result.size == len(content)


@pytest.mark.parametrize(
    "content",
    [
        _zip_bytes({"a.txt": b"hello"}),
        _zip_bytes({"[Content_Types].xml": b"<Types/>"}),  # 缺 xl/
        _zip_bytes({"xl/workbook.xml": b"<x/>"}),  # 缺 [Content_Types].xml
        _zip_bytes({"[Content_Types].xml": b"<Types/>", "word/document.xml": b"<d/>"}),  # docx
        b"PK\x03\x04" + b"0" * 100,  # 壞掉的 zip
    ],
)
def test_uploads_non_xlsx_zip_rejected(content: bytes) -> None:
    with pytest.raises(AppError) as exc_info:
        read_validated_upload(_upload(content, "a.xlsx"), allowed=XLSX_TYPES, max_bytes=_MB)

    assert _app_error(exc_info) == ("unsupported_file_type", 415)


def test_uploads_constants() -> None:
    assert frozenset({"jpg", "png", "webp"}) == IMAGE_TYPES
    assert frozenset({"jpg", "png", "webp", "heic", "pdf"}) == ATTACHMENT_TYPES
    assert frozenset({"xlsx"}) == XLSX_TYPES
    # 與 DB-034 bucket file_size_limit 一致
    assert PHOTO_MAX_BYTES == 5242880
    assert ATTACHMENT_MAX_BYTES == 10485760
    assert XLSX_MAX_BYTES == 5242880


def test_uploads_reads_spooled_file() -> None:
    """endpoint 實際拿到的是 SpooledTemporaryFile；確認不依賴 BytesIO 特性。"""
    with tempfile.SpooledTemporaryFile(max_size=10) as spooled:
        spooled.write(_PNG)
        spooled.seek(0)

        result = read_validated_upload(
            UploadFile(file=spooled, filename="a.png"), allowed=IMAGE_TYPES, max_bytes=_MB
        )

    assert (result.ext, result.size) == ("png", len(_PNG))
