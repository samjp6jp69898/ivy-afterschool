"""BACKEND-155：學生 Excel 匯入預覽 ``preview``（domain_spec §2 ``POST /students/import`` 兩段式的
第一段：解析 + 逐列驗證，不寫入）。移植 ivy ``utils/excel_io.py::parse_excel`` /
``assert_xlsx_not_zip_bomb`` 的防護，欄位改為本專案學生欄位。

- 呼叫端先以 BACKEND-016 ``read_validated_upload``（XLSX 檔頭 + 5 MB 上限）取得
  ``ValidatedUpload``；本模組在交給 openpyxl 之前再做 zip / XML 層防護
  （``assert_xlsx_within_limits``，不受信任的 xlsx 可以只有幾十 KB 卻讓解析器吃掉上 GB
  記憶體）。安全硬上限集中為模組常數
  （不是業務參數，不放 system_settings）：
  - zip：entry 數 ≤ ``MAX_ZIP_ENTRIES``、同名 entry 拒收、只接受 stored / deflate、加密 entry 拒收、
    單一 entry 解壓 ≤ ``MAX_ZIP_ENTRY_BYTES``、總解壓 ≤ ``MAX_XLSX_UNCOMPRESSED_BYTES``（先看
    宣告值再串流讀，不先配置記憶體）；宣告大小 / CRC 不符、截斷、不支援的壓縮法等一律 422
    ``import_invalid_file``。全程在記憶體內處理，不解壓到磁碟（路徑穿越檔名不適用）。
  - XML：zip 內**每一個**內容以 ``<`` 開頭的 entry（不依檔名 / 副檔名過濾，工作表、styles、
    sharedStrings、workbook、rels、Content_Types、docProps、externalLinks 都算；把工作表改放到
    worksheets/ 以外的路徑也逃不掉）都以 ``xml.parsers.expat`` 串流掃描、只計數不建樹：元素總數 ≤
    ``MAX_XML_ELEMENTS``、巢狀深度 ≤ ``MAX_XML_DEPTH``、單一屬性值 / 文字節點長度 ≤
    ``MAX_XML_VALUE_CHARS``；另依 local name 計 row / c（命名空間前綴、換行 / tab 都算）：列數 ≤
    ``MAX_IMPORT_ROWS`` + 寬容值、單列 ≤ ``MAX_IMPORT_COLS``、總儲存格 ≤ 列數乘欄數。任一超標即
    中止，記憶體有界。expat 不載入外部實體、拒絕實體膨脹（billion laughs）。
  - openpyxl：``read_only`` + ``data_only``（公式只取快取值，儲存格內容一律當純文字）+
    ``keep_links=False``（不處理外部連結）；丟棄可偽造的 dimension、以 max_row / max_col 硬上限限制
    迭代量；載入與迭代階段的任何例外（sharedStrings 索引越界、壞日期等）一律 422
    ``import_invalid_file``。取**第一張可見**（``sheet_state == "visible"``）的工作表，沒有可見
    工作表 → 422 ``import_invalid_file``。
- 標題列：``check_header`` 回 ``(missing, unexpected)``（必填欄缺少依 IMPORT_COLUMNS 順序、無法辨識
  的欄名依標題列順序；選填欄缺少不算錯）→ 422 ``import_invalid_header``。資料列 > 500 → 422
  ``import_too_many_rows``；沒有資料列（整列空白不算）→ 422 ``import_empty``。
- 逐列：性別 / 狀態中文對照、日期接受 Excel 日期或 ``YYYY-MM-DD`` / ``YYYY/MM/DD``、國小以 name 或
  short_name 比對啟用中的 schools（不分大小寫）、班級以名稱比對該學年度未封存的班、身分證正規化 +
  檢查碼（BACKEND-147）、無 ``students:sensitive`` 不可帶身分證 / 健康備註、學號與身分證 HMAC
  的 DB 重複（含封存）與檔內重複（兩列都標），最後以 ``StudentCreateIn`` 驗證並把 Pydantic 錯誤
  轉成繁中訊息。驗證規則與 BACKEND-151 ``create_student`` 一致（執行階段由 BACKEND-156 共用其
  寫入邏輯）。
- ``display`` 為原始儲存格字串供前端呈現：身分證遮罩（``mask_id_number``）、健康備註以固定文字
  取代。
"""

from __future__ import annotations

import io
import re
import zipfile
import zlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import IO, Any, Final
from uuid import UUID
from xml.parsers import expat

from openpyxl import load_workbook
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import CurrentStaff
from app.core.errors import AppError
from app.core.permissions import Permission
from app.core.uploads import ValidatedUpload
from app.models.classes import SchoolClass
from app.models.reference import School
from app.models.students import Student
from app.schemas.students import StudentCreateIn
from app.services.students.id_number import (
    id_number_hmac,
    mask_id_number,
    normalize_id_number,
    validate_id_number,
)

IMPORT_COLUMNS: Final[list[str]] = [
    "學號*",
    "姓名*",
    "性別",
    "生日",
    "年級*",
    "就讀國小",
    "學校班級",
    "安親班班級",
    "狀態",
    "入學日",
    "備註",
    "身分證字號",
    "健康備註",
]
MAX_IMPORT_ROWS: Final = 500
MAX_IMPORT_COLS: Final = 64
# 標題列 + 少量空白列的寬容值；超過就不再往下讀
_GRID_ROW_SLACK: Final = 16

# --- 不受信任 xlsx 的安全硬上限（模組常數；合理的匯入檔遠低於這些值）---
MAX_ZIP_ENTRIES: Final = 64  # openpyxl 產生的 xlsx 約 10 個 entry
MAX_ZIP_ENTRY_BYTES: Final = 16 * 1024 * 1024  # 單一 entry 解壓後
MAX_XLSX_UNCOMPRESSED_BYTES: Final = 32 * 1024 * 1024  # 全部 entry 解壓後總和
# 單一 XML part 的元素總數：517 列乘 64 欄、每格 2~3 個元素，再留餘裕
MAX_XML_ELEMENTS: Final = 250_000
MAX_XML_DEPTH: Final = 32  # 單一 XML part 的巢狀深度（正常工作表約 6 層）
MAX_XML_VALUE_CHARS: Final = 32 * 1024  # 單一屬性值 / 連續文字節點的字元數
_ALLOWED_COMPRESSION: Final = frozenset({zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED})
_ZIP_ENCRYPTED_FLAG: Final = 0x1

_REQUIRED_COLUMNS: Final = [c for c in IMPORT_COLUMNS if c.endswith("*")]
_COLUMN_FIELDS: Final[dict[str, str]] = {
    "學號*": "student_no",
    "姓名*": "name",
    "性別": "gender",
    "生日": "birthday",
    "年級*": "grade_level",
    "就讀國小": "school_id",
    "學校班級": "school_class",
    "安親班班級": "class_id",
    "狀態": "status",
    "入學日": "enrolled_on",
    "備註": "note",
    "身分證字號": "id_number",
    "健康備註": "health_note",
}
_FIELD_COLUMNS: Final = {field: column for column, field in _COLUMN_FIELDS.items()}
_GENDERS: Final = {"男": "male", "女": "female", "其他": "other"}
_STATUSES: Final = {"在學": "active", "暫停": "suspended", "退班": "withdrawn"}
_DATE_RE: Final = re.compile(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})$")
_HEALTH_NOTE_MASK: Final = "（已隱藏）"
_SENSITIVE_PERMISSION_ERROR: Final = "沒有權限匯入敏感欄位"

# zip / XML 解析階段可能拋出的錯誤：壞檔一律 422，不讓 500 外洩
_ZIP_ERRORS: Final = (
    zipfile.BadZipFile,
    zipfile.LargeZipFile,
    zlib.error,
    EOFError,
    NotImplementedError,
    OSError,
    ValueError,
    expat.ExpatError,
)


@dataclass(frozen=True)
class ImportRowResult:
    row_number: int  # Excel 列號（標題為第 1 列）
    data: StudentCreateIn | None  # 驗證通過時的正規化資料
    display: dict[str, str]  # 原始儲存格字串（敏感欄位遮罩），供前端呈現
    errors: list[str]  # 繁中錯誤訊息


@dataclass(frozen=True)
class ImportPreview:
    rows: list[ImportRowResult]
    total: int
    valid: int
    invalid: int


def _invalid_file(message: str) -> AppError:
    return AppError("import_invalid_file", message, status=422)


# --- 檔案層防護 ------------------------------------------------------------------------


def _local_name(tag: str) -> str:
    # expat 不開 namespace 處理時 tag 為原字串，可能帶 ``x:`` 前綴
    return tag.rsplit(":", 1)[-1]


def _too_many_rows() -> AppError:
    return AppError(
        "import_too_many_rows",
        f"匯入列數超過上限 {MAX_IMPORT_ROWS}，請分批匯入",
        status=422,
        details={"max_rows": MAX_IMPORT_ROWS},
    )


class _XmlLimitScanner:
    """expat 串流掃描單一 XML part：只計數、不建樹；任一上限超過即拋 AppError（422）。

    通用上限：元素總數、巢狀深度、單一屬性值 / 連續文字節點長度。工作表上限：row / c 的 local name
    計數（命名空間前綴與元素名後的空白都算得到）。expat 的 UseForeignDTD / 外部實體預設不載入；
    內建 expat 2.4.1+ 預設拒絕實體膨脹攻擊。
    """

    def __init__(self) -> None:
        self.elements = 0
        self.depth = 0
        self.rows = 0
        self.cells = 0
        self.row_cells = 0
        self._text_len = 0
        parser = expat.ParserCreate()
        parser.buffer_text = False
        parser.StartElementHandler = self._start
        parser.EndElementHandler = self._end
        parser.CharacterDataHandler = self._text
        parser.ExternalEntityRefHandler = self._reject_external_entity
        self._parser = parser

    def feed_head(self, head: bytes) -> None:
        self._parser.Parse(head, False)

    def feed(self, stream: IO[bytes]) -> None:
        while chunk := stream.read(64 * 1024):
            self._parser.Parse(chunk, False)
        self._parser.Parse(b"", True)

    # --- handlers ---
    def _reject_external_entity(self, *_: object) -> int:
        raise _invalid_file("Excel 內含外部實體參照，請確認檔案內容")

    def _start(self, name: str, attrs: dict[str, str]) -> None:
        self._text_len = 0
        self.elements += 1
        if self.elements > MAX_XML_ELEMENTS:
            raise _invalid_file("Excel 內容元素數量超過上限，請確認檔案內容")
        self.depth += 1
        if self.depth > MAX_XML_DEPTH:
            raise _invalid_file("Excel 內容巢狀層數超過上限，請確認檔案內容")
        for value in attrs.values():
            if len(value) > MAX_XML_VALUE_CHARS:
                raise _invalid_file("Excel 內含過長的屬性值，請確認檔案內容")
        local = _local_name(name)
        if local == "row":
            self.rows += 1
            self.row_cells = 0
            if self.rows > MAX_IMPORT_ROWS + _GRID_ROW_SLACK:
                raise _too_many_rows()
        elif local == "c":
            self.cells += 1
            self.row_cells += 1
            if self.row_cells > MAX_IMPORT_COLS:
                raise _invalid_file(
                    f"Excel 欄位數超過上限 {MAX_IMPORT_COLS}，請確認檔案未含異常寬列"
                )
            if self.cells > MAX_IMPORT_ROWS * MAX_IMPORT_COLS:
                raise _invalid_file("Excel 儲存格數量超過上限，請確認檔案內容")

    def _end(self, _name: str) -> None:
        self._text_len = 0
        self.depth -= 1

    def _text(self, data: str) -> None:
        # 同一文字節點 expat 可能分多次回呼：累計到遇到下一個元素邊界為止
        self._text_len += len(data)
        if self._text_len > MAX_XML_VALUE_CHARS:
            raise _invalid_file("Excel 內含過長的文字內容，請確認檔案內容")


def _looks_like_xml(head: bytes) -> bool:
    """內容以 ``<`` 開頭（允許 UTF-8 BOM 與前置空白）就當 XML 掃；其餘（圖片等二進位）只受大小限制，
    openpyxl 也不會把它們當 XML 解析。"""
    return head.removeprefix(b"\xef\xbb\xbf").lstrip().startswith(b"<")


def _check_zip_directory(infos: list[zipfile.ZipInfo]) -> None:
    if len(infos) > MAX_ZIP_ENTRIES:
        raise _invalid_file(f"Excel 內含過多檔案（上限 {MAX_ZIP_ENTRIES}），請確認檔案內容")
    seen: set[str] = set()
    total = 0
    for info in infos:
        # 不解壓到磁碟，檔名只用來偵測同名 entry（openpyxl 以名稱取 part，同名會取到哪一份不確定）
        name = info.filename.replace("\\", "/")
        if name in seen:
            raise _invalid_file("Excel 內含重複的檔案項目，請確認檔案內容")
        seen.add(name)
        if info.flag_bits & _ZIP_ENCRYPTED_FLAG:
            raise _invalid_file("Excel 內含加密內容，無法讀取")
        if info.compress_type not in _ALLOWED_COMPRESSION:
            raise _invalid_file("Excel 使用不支援的壓縮方式，無法讀取")
        if info.file_size > MAX_ZIP_ENTRY_BYTES:
            raise _invalid_file("Excel 內含過大的檔案項目（疑似壓縮炸彈），請確認檔案內容")
        total += info.file_size
        if total > MAX_XLSX_UNCOMPRESSED_BYTES:
            raise _invalid_file("Excel 解壓後大小超過上限（疑似壓縮炸彈），請確認檔案內容")


def assert_xlsx_within_limits(content: bytes) -> None:
    """交給 openpyxl 之前的 zip / XML 層防護；任何壞檔或超標一律 422（見模組 docstring）。"""
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            infos = zf.infolist()
            _check_zip_directory(infos)
            for info in infos:
                if info.file_size == 0:
                    continue
                with zf.open(info) as stream:
                    head = stream.read(64)
                    if not _looks_like_xml(head):
                        # 非 XML：只需確認實際解壓大小與宣告一致（讀完由 zipfile 驗 CRC）
                        while stream.read(64 * 1024):
                            pass
                        continue
                    scanner = _XmlLimitScanner()
                    scanner.feed_head(head)
                    scanner.feed(stream)
    except AppError:
        raise
    except _ZIP_ERRORS:
        raise _invalid_file("無法讀取 Excel 檔案") from None


# --- 標題與讀列 ------------------------------------------------------------------------


def _header_text(cell: object) -> str:
    return "" if cell is None else str(cell).strip()


def check_header(header_cells: Sequence[str | None]) -> tuple[list[str], list[str]]:
    """回 ``(missing, unexpected)``：缺少的必填欄（IMPORT_COLUMNS 順序）、無法辨識的非空白欄名
    （標題列順序）。選填欄缺少不算錯。"""
    present = [_header_text(c) for c in header_cells]
    missing = [c for c in _REQUIRED_COLUMNS if c not in present]
    unexpected = [c for c in present if c and c not in IMPORT_COLUMNS]
    return missing, unexpected


def _read_rows(content: bytes) -> tuple[list[str], list[tuple[int, tuple[Any, ...]]]]:
    """第一個工作表 → (標題, [(Excel 列號, 儲存格值...)])；整列空白略過。"""
    assert_xlsx_within_limits(content)
    try:
        wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True, keep_links=False)
    except Exception:  # openpyxl 對壞檔會拋各種解析例外，一律視為無法讀取
        raise _invalid_file("無法讀取 Excel 檔案") from None
    try:
        header, rows = _iterate_rows(wb)
    except AppError:
        raise
    except Exception:  # sharedStrings 索引越界、壞日期等解析期例外一律 422，不讓 500 外洩
        raise _invalid_file("無法讀取 Excel 檔案") from None
    finally:
        wb.close()
    if not rows:
        raise AppError("import_empty", "Excel 沒有資料列", status=422)
    return header, rows


def _iterate_rows(wb: Any) -> tuple[list[str], list[tuple[int, tuple[Any, ...]]]]:
    """第一張可見工作表 → (標題, [(Excel 列號, 儲存格值...)])；整列空白略過。"""
    ws = next((w for w in wb.worksheets if w.sheet_state == "visible"), None)
    if ws is None:
        raise _invalid_file("Excel 沒有可見的工作表")
    # 丟棄可偽造的 dimension（否則 read_only 會依宣告的末列補出大量空白列），並以硬上限限制
    # 迭代量
    ws.reset_dimensions()
    rows_iter = ws.iter_rows(
        values_only=True,
        max_row=MAX_IMPORT_ROWS + _GRID_ROW_SLACK + 1,
        max_col=MAX_IMPORT_COLS,  # read_only 會補到 max_col；超寬列已由 zip 層掃描擋下
    )
    header_row = next(rows_iter, None)
    if header_row is None or not any(_header_text(c) for c in header_row):
        raise AppError("import_empty", "Excel 沒有資料列", status=422)
    header = [_header_text(c) for c in header_row]
    if len(header) > MAX_IMPORT_COLS:
        raise _invalid_file(f"Excel 欄位數超過上限 {MAX_IMPORT_COLS}")
    missing, unexpected = check_header(header)
    if missing or unexpected:
        raise AppError(
            "import_invalid_header",
            "Excel 標題列與匯入範本不符",
            status=422,
            details={"missing": missing, "unexpected": unexpected},
        )
    rows: list[tuple[int, tuple[Any, ...]]] = []
    for row_number, raw in enumerate(rows_iter, start=2):
        if raw is None or all(v is None or (isinstance(v, str) and not v.strip()) for v in raw):
            continue
        rows.append((row_number, tuple(raw)))
        if len(rows) > MAX_IMPORT_ROWS:
            raise _too_many_rows()
    return header, rows


# --- 儲存格轉換 ------------------------------------------------------------------------


def _cell_text(value: object) -> str | None:
    """儲存格 → 去頭尾空白的字串；空白視為未填。整數值的浮點數（Excel 數字）去掉 .0。"""
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, datetime):
        value = value.date()
    text = str(value).strip()
    return text or None


def _parse_date(value: object, column: str, errors: list[str]) -> date | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    match = _DATE_RE.match(str(value).strip())
    if match is not None:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            pass
    errors.append(f"{column}格式不正確（請用 YYYY-MM-DD）")
    return None


def _parse_choice(
    text: str | None, mapping: Mapping[str, str], column: str, errors: list[str]
) -> str | None:
    if text is None:
        return None
    mapped = mapping.get(text)
    if mapped is None:
        errors.append(f"{column}只能填 {' / '.join(mapping)}")
    return mapped


def _parse_grade(text: str | None, errors: list[str]) -> int | None:
    if text is None:
        return None
    if not text.isdigit():
        errors.append("年級*必須是 1~6 的整數")
        return None
    return int(text)


def _display_value(column: str, value: object) -> str:
    text = _cell_text(value)
    if text is None:
        return ""
    if column == "身分證字號":
        normalized = normalize_id_number(text)
        return mask_id_number(normalized) if len(normalized) >= 7 else "***"
    if column == "健康備註":
        return _HEALTH_NOTE_MASK
    return text


def _validation_messages(exc: ValidationError) -> list[str]:
    messages: list[str] = []
    for err in exc.errors():
        loc = err.get("loc", ())
        field = str(loc[0]) if loc else ""
        column = _FIELD_COLUMNS.get(field, field)
        messages.append(f"{column}格式不正確：{err.get('msg', '')}")
    return messages


# --- 查表 ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Lookups:
    schools: dict[str, UUID]  # casefold(name / short_name) → id（只含啟用中）
    classes: dict[str, UUID]  # casefold(name) → id（該學年度未封存）
    existing_student_nos: set[str]
    existing_hmacs: set[str]


def _load_lookups(
    session: Session, *, academic_year: int, student_nos: Iterable[str], hmacs: Iterable[str]
) -> _Lookups:
    schools: dict[str, UUID] = {}
    for school in session.execute(select(School).where(School.is_active.is_(True))).scalars():
        schools[school.name.strip().casefold()] = school.id
        if school.short_name:
            schools.setdefault(school.short_name.strip().casefold(), school.id)
    classes = {
        name.strip().casefold(): class_id
        for class_id, name in session.execute(
            select(SchoolClass.id, SchoolClass.name).where(
                SchoolClass.academic_year == academic_year, SchoolClass.archived_at.is_(None)
            )
        )
    }
    nos = list(set(student_nos))
    existing_nos: set[str] = set()
    if nos:
        existing_nos = set(
            session.execute(select(Student.student_no).where(Student.student_no.in_(nos))).scalars()
        )
    hmac_list = list(set(hmacs))
    existing_hmacs: set[str] = set()
    if hmac_list:
        existing_hmacs = {
            h
            for h in session.execute(
                select(Student.id_number_hmac).where(Student.id_number_hmac.in_(hmac_list))
            ).scalars()
            if h is not None
        }
    return _Lookups(schools, classes, existing_nos, existing_hmacs)


# --- preview ----------------------------------------------------------------------


def _cells(header: list[str], raw: tuple[Any, ...]) -> dict[str, Any]:
    """依標題名取值（忽略空白標題欄）。"""
    return {name: (raw[i] if i < len(raw) else None) for i, name in enumerate(header) if name}


def preview(
    session: Session, upload: ValidatedUpload, *, academic_year: int, actor: CurrentStaff
) -> ImportPreview:
    header, raw_rows = _read_rows(upload.content)
    rows = [(row_number, _cells(header, raw)) for row_number, raw in raw_rows]
    can_sensitive = actor.has(Permission.STUDENTS_SENSITIVE)

    # 先算學號與身分證 HMAC：一次查 DB、同時找出檔內重複
    student_nos = [_cell_text(cells.get("學號*")) for _, cells in rows]
    normalized_ids: list[str | None] = []
    hmacs: list[str | None] = []
    for _, cells in rows:
        raw_id = _cell_text(cells.get("身分證字號"))
        normalized = normalize_id_number(raw_id) if raw_id is not None else None
        normalized_ids.append(normalized)
        hmacs.append(id_number_hmac(normalized) if normalized is not None else None)
    lookups = _load_lookups(
        session,
        academic_year=academic_year,
        student_nos=[n for n in student_nos if n is not None],
        hmacs=[h for h in hmacs if h is not None],
    )
    no_counts: dict[str, int] = {}
    for no in student_nos:
        if no is not None:
            no_counts[no] = no_counts.get(no, 0) + 1
    hmac_counts: dict[str, int] = {}
    for hmac in hmacs:
        if hmac is not None:
            hmac_counts[hmac] = hmac_counts.get(hmac, 0) + 1

    results: list[ImportRowResult] = []
    for index, (row_number, cells) in enumerate(rows):
        errors: list[str] = []
        display = {column: _display_value(column, cells.get(column)) for column in IMPORT_COLUMNS}

        student_no = student_nos[index]
        if student_no is not None:
            if student_no in lookups.existing_student_nos:
                errors.append("學號已存在")
            if no_counts.get(student_no, 0) > 1:
                errors.append("學號在檔案中重複")

        school_text = _cell_text(cells.get("就讀國小"))
        school_id: UUID | None = None
        if school_text is not None:
            school_id = lookups.schools.get(school_text.casefold())
            if school_id is None:
                errors.append(f"找不到國小：{school_text}")
        class_text = _cell_text(cells.get("安親班班級"))
        class_id: UUID | None = None
        if class_text is not None:
            class_id = lookups.classes.get(class_text.casefold())
            if class_id is None:
                errors.append(f"找不到安親班班級：{class_text}（{academic_year} 學年度）")

        health_note = _cell_text(cells.get("健康備註"))
        normalized_id = normalized_ids[index]
        if (normalized_id is not None or health_note is not None) and not can_sensitive:
            errors.append(_SENSITIVE_PERMISSION_ERROR)
        if normalized_id is not None:
            try:
                validate_id_number(normalized_id)
            except AppError:
                errors.append("身分證字號格式不正確")
            else:
                hmac = hmacs[index]
                if hmac in lookups.existing_hmacs:
                    errors.append("身分證字號已存在")
                if hmac is not None and hmac_counts.get(hmac, 0) > 1:
                    errors.append("身分證字號在檔案中重複")

        payload: dict[str, Any] = {
            "student_no": student_no,
            "name": _cell_text(cells.get("姓名*")),
            "gender": _parse_choice(_cell_text(cells.get("性別")), _GENDERS, "性別", errors),
            "birthday": _parse_date(cells.get("生日"), "生日", errors),
            "grade_level": _parse_grade(_cell_text(cells.get("年級*")), errors),
            "school_id": school_id,
            "school_class": _cell_text(cells.get("學校班級")),
            "class_id": class_id,
            "status": _parse_choice(_cell_text(cells.get("狀態")), _STATUSES, "狀態", errors)
            or "active",
            "enrolled_on": _parse_date(cells.get("入學日"), "入學日", errors),
            "note": _cell_text(cells.get("備註")),
            "id_number": normalized_id,
            "health_note": health_note,
        }
        data: StudentCreateIn | None = None
        try:
            data = StudentCreateIn.model_validate(payload)
        except ValidationError as exc:
            errors.extend(_validation_messages(exc))
        if errors:
            data = None
        results.append(
            ImportRowResult(row_number=row_number, data=data, display=display, errors=errors)
        )

    valid = sum(1 for r in results if r.data is not None)
    return ImportPreview(
        rows=results, total=len(results), valid=valid, invalid=len(results) - valid
    )


__all__ = [
    "IMPORT_COLUMNS",
    "MAX_IMPORT_ROWS",
    "ImportPreview",
    "ImportRowResult",
    "assert_xlsx_within_limits",
    "check_header",
    "preview",
]
