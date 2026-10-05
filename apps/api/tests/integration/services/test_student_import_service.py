"""BACKEND-155：app/services/student_import_service.py（preview：解析 Excel、逐列驗證、不寫入）。"""

from __future__ import annotations

import io
from collections.abc import Iterator, Sequence
from datetime import date
from uuid import uuid4

import pytest
from openpyxl import Workbook
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import CurrentStaff
from app.core.config import get_settings
from app.core.crypto import derive_key, encrypt_bytes
from app.core.errors import AppError
from app.core.uploads import ValidatedUpload
from app.models.students import Student
from app.schemas.students import StudentCreateIn
from app.services.student_import_service import (
    IMPORT_COLUMNS,
    MAX_IMPORT_ROWS,
    ImportPreview,
    ImportRowResult,
    check_header,
    preview,
)
from app.services.students.id_number import id_number_hmac
from tests.support.factories import make_class, make_school, make_student

_ID_A = "A123456789"
_ID_B = "B123456708"


@pytest.fixture(autouse=True)
def _crypto_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@127.0.0.1:54342/postgres")
    monkeypatch.setenv("APP_SECRET_KEY", "s" * 48)
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://127.0.0.1:5341")
    monkeypatch.setenv("R2_ENDPOINT_URL", "http://127.0.0.1:54344")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "afterschool")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "afterschool-local-secret")
    monkeypatch.setenv("R2_BUCKET", "afterschool-local")
    get_settings.cache_clear()
    derive_key.cache_clear()
    yield
    get_settings.cache_clear()
    derive_key.cache_clear()


def _actor(*permissions: str) -> CurrentStaff:
    return CurrentStaff(
        id=uuid4(),
        username="clerk",
        display_name="陳行政",
        role_id=uuid4(),
        role_code="clerk",
        role_name="行政",
        permissions=frozenset(permissions),
        must_change_password=False,
        token_version=0,
    )


_WRITER = _actor("students:write")
_SENSITIVE = _actor("students:write", "students:sensitive")


def _xlsx(rows: Sequence[Sequence[object]], header: Sequence[str] | None = None) -> ValidatedUpload:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    if header is not None:
        ws.append(list(header))
    for row in rows:
        ws.append(list(row))
    buf = io.BytesIO()
    wb.save(buf)
    content = buf.getvalue()
    return ValidatedUpload(
        content=content,
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ext="xlsx",
        size=len(content),
    )


def _row(  # 測試資料列：欄位順序對齊 IMPORT_COLUMNS
    student_no: object = "S115101",
    name: object = "林小安",
    gender: object = "男",
    birthday: object = date(2018, 5, 1),
    grade: object = 2,
    school: object = "新生",
    school_class: object = "二年三班",
    klass: object = "低年級 A 班",
    status: object = "在學",
    enrolled: object = "2026-09-01",
    note: object = None,
    id_number: object = None,
    health_note: object = None,
) -> list[object]:
    return [
        student_no,
        name,
        gender,
        birthday,
        grade,
        school,
        school_class,
        klass,
        status,
        enrolled,
        note,
        id_number,
        health_note,
    ]


@pytest.fixture
def lookups(db_session: Session) -> dict[str, object]:
    school = make_school(db_session, name="新生國小")
    school.short_name = "新生"
    inactive = make_school(db_session, name="停用國小")
    inactive.is_active = False
    klass = make_class(db_session, name="低年級 A 班", academic_year=115)
    make_class(db_session, name="低年級 B 班", academic_year=114)  # 別的學年
    make_class(db_session, name="舊班", academic_year=115, archived=True)
    db_session.flush()
    return {"school": school, "class": klass}


def _preview(
    db: Session, upload: ValidatedUpload, actor: CurrentStaff = _SENSITIVE
) -> ImportPreview:
    return preview(db, upload, academic_year=115, actor=actor)


def test_import_preview_valid(db_session: Session, lookups: dict[str, object]) -> None:
    upload = _xlsx(
        [
            _row(id_number=f" {_ID_A.lower()} ", health_note="氣喘"),
            _row(
                student_no="S115102", name="陳小華", gender="女", school="新生國小", status="暫停"
            ),
            _row(
                student_no=115103,  # 數字儲存格
                name="黃小美",
                gender="其他",
                birthday="2017/03/15",
                grade=3.0,
                school=None,
                klass=None,
                status=None,
                enrolled=date(2025, 9, 1),
                note="備註",
            ),
        ],
        header=IMPORT_COLUMNS,
    )

    result = _preview(db_session, upload)

    assert isinstance(result, ImportPreview)
    assert (result.total, result.valid, result.invalid) == (3, 3, 0)
    first, second, third = result.rows
    assert isinstance(first, ImportRowResult)
    assert [r.row_number for r in result.rows] == [2, 3, 4]
    assert all(r.errors == [] for r in result.rows)
    assert isinstance(first.data, StudentCreateIn)
    assert first.data.student_no == "S115101"
    assert first.data.gender == "male"
    assert first.data.birthday == date(2018, 5, 1)
    assert first.data.class_id == lookups["class"].id  # type: ignore[attr-defined]
    assert first.data.school_id == lookups["school"].id  # type: ignore[attr-defined]
    assert first.data.school_class == "二年三班"
    assert first.data.status == "active"
    assert first.data.enrolled_on == date(2026, 9, 1)
    assert first.data.id_number == _ID_A  # 正規化（去空白、大寫）
    assert first.data.health_note == "氣喘"
    assert first.display["學號*"] == "S115101"
    assert first.display["身分證字號"] == "A12****789"
    assert _ID_A not in str(first.display)
    assert "氣喘" not in str(first.display)
    assert second.data is not None
    assert (second.data.gender, second.data.status) == ("female", "suspended")
    assert second.data.school_id == lookups["school"].id  # type: ignore[attr-defined]
    assert third.data is not None
    assert third.data.student_no == "115103"
    assert (third.data.gender, third.data.grade_level) == ("other", 3)
    assert third.data.birthday == date(2017, 3, 15)
    assert third.data.enrolled_on == date(2025, 9, 1)
    assert (third.data.school_id, third.data.class_id, third.data.status) == (None, None, "active")
    assert third.data.note == "備註"
    assert third.display["姓名*"] == "黃小美"


def test_import_preview_header_and_limits(db_session: Session) -> None:
    missing_name = [c for c in IMPORT_COLUMNS if c != "姓名*"]
    with pytest.raises(AppError) as missing:
        _preview(db_session, _xlsx([_row()[:1] + _row()[2:]], header=missing_name))
    assert (missing.value.status, missing.value.code) == (422, "import_invalid_header")
    assert missing.value.details == {"missing": ["姓名*"], "unexpected": []}

    with pytest.raises(AppError) as extra:
        _preview(db_session, _xlsx([[*_row(), 5]], header=[*IMPORT_COLUMNS, "座號"]))
    assert extra.value.code == "import_invalid_header"
    assert extra.value.details == {"missing": [], "unexpected": ["座號"]}
    # 標題去頭尾空白比對；選填欄缺少不算錯
    assert check_header([" 學號* ", "姓名*", "年級*", None, ""]) == ([], [])
    assert check_header(["學號*", "座號", "姓名*"]) == (["年級*"], ["座號"])

    too_many = [_row(student_no=f"S{n:06d}") for n in range(MAX_IMPORT_ROWS + 1)]
    with pytest.raises(AppError) as limit:
        _preview(db_session, _xlsx(too_many, header=IMPORT_COLUMNS))
    assert (limit.value.status, limit.value.code) == (422, "import_too_many_rows")

    with pytest.raises(AppError) as empty:
        _preview(db_session, _xlsx([], header=IMPORT_COLUMNS))
    assert (empty.value.status, empty.value.code) == (422, "import_empty")
    # 全空白列不算資料列；完全沒有標題也是空檔
    with pytest.raises(AppError) as blank:
        _preview(db_session, _xlsx([[None] * len(IMPORT_COLUMNS)], header=IMPORT_COLUMNS))
    assert blank.value.code == "import_empty"
    with pytest.raises(AppError) as no_header:
        _preview(db_session, _xlsx([]))
    assert no_header.value.code == "import_empty"


def test_import_preview_lookup_errors(db_session: Session, lookups: dict[str, object]) -> None:
    upload = _xlsx(
        [
            _row(school="不存在國小"),
            _row(student_no="S115102", klass="不存在班"),
            _row(student_no="S115103", school="停用國小", klass="低年級 B 班"),
            _row(student_no="S115104", klass="舊班"),
            _row(student_no="S115105"),
        ],
        header=IMPORT_COLUMNS,
    )

    result = _preview(db_session, upload)

    assert (result.total, result.valid, result.invalid) == (5, 1, 4)
    assert "找不到國小：不存在國小" in result.rows[0].errors
    assert result.rows[0].data is None
    assert any("找不到安親班班級" in e for e in result.rows[1].errors)
    assert any("找不到國小" in e for e in result.rows[2].errors)  # 停用的國小不算
    assert any("找不到安親班班級" in e for e in result.rows[2].errors)  # 別的學年
    assert any("找不到安親班班級" in e for e in result.rows[3].errors)  # 已封存
    assert result.rows[4].errors == []
    assert result.rows[4].data is not None


def test_import_preview_duplicates(db_session: Session, lookups: dict[str, object]) -> None:
    make_student(db_session, student_no="S115001")
    archived = make_student(db_session, student_no="OLD001", archived=True)
    archived.id_number_enc = encrypt_bytes(_ID_A)
    archived.id_number_hmac = id_number_hmac(_ID_A)
    db_session.flush()
    upload = _xlsx(
        [
            _row(student_no="S115001"),
            _row(student_no="S115200", name="甲"),
            _row(student_no="S115200", name="乙"),
            _row(student_no="S115201", id_number=_ID_A),
            _row(student_no="S115202", id_number=_ID_B),
            _row(student_no="S115203", id_number=_ID_B.lower()),
            _row(student_no="S115204", id_number="A123"),
            _row(student_no="S 115205"),
        ],
        header=IMPORT_COLUMNS,
    )

    result = _preview(db_session, upload)

    rows = result.rows
    assert "學號已存在" in rows[0].errors
    assert "學號在檔案中重複" in rows[1].errors
    assert "學號在檔案中重複" in rows[2].errors
    assert "身分證字號已存在" in rows[3].errors
    assert "身分證字號在檔案中重複" in rows[4].errors
    assert "身分證字號在檔案中重複" in rows[5].errors
    assert any("身分證字號" in e and "格式" in e for e in rows[6].errors)
    assert any("學號" in e for e in rows[7].errors)
    assert result.valid == 0
    assert result.invalid == 8


def test_import_preview_sensitive_permission(
    db_session: Session, lookups: dict[str, object]
) -> None:
    upload = _xlsx(
        [
            _row(id_number=_ID_A),
            _row(student_no="S115102", health_note="對花生過敏"),
            _row(student_no="S115103"),
        ],
        header=IMPORT_COLUMNS,
    )

    result = _preview(db_session, upload, actor=_WRITER)

    assert "沒有權限匯入敏感欄位" in result.rows[0].errors
    assert "沒有權限匯入敏感欄位" in result.rows[1].errors
    assert result.rows[2].errors == []
    assert result.rows[0].display["身分證字號"] == "A12****789"
    assert "對花生過敏" not in str(result.rows[1].display)
    assert result.rows[1].display["健康備註"] != ""
    assert (result.valid, result.invalid) == (1, 2)
    # 有權限時同一檔全數通過
    allowed = _preview(db_session, upload, actor=_SENSITIVE)
    assert allowed.invalid == 0


def test_import_preview_no_writes(db_session: Session, lookups: dict[str, object]) -> None:
    before = db_session.execute(select(func.count()).select_from(Student)).scalar_one()
    upload = _xlsx([_row(), _row(student_no="S115102", id_number=_ID_A)], header=IMPORT_COLUMNS)

    result = _preview(db_session, upload)

    assert result.valid == 2
    assert not db_session.new
    assert db_session.execute(select(func.count()).select_from(Student)).scalar_one() == before
