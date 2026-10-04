"""DB-019：student_leave_attachments 表（路徑唯一與格式、mime 白名單、大小上限、cascade）。"""

from uuid import uuid4

from tests.integration.db.conftest import (
    CHECK_VIOLATION,
    SEEDED_UPDATED_AT,
    UNIQUE_VIOLATION,
    Conn,
    assert_backend_grants,
    assert_backend_read_write,
    assert_updated_at_trigger,
    pg_error,
)
from tests.integration.db.factories import make_student_leave_attachments, make_student_leaves

MAX_SIZE = 10485760


def test_student_leave_attachments_path_unique(backend_conn: Conn) -> None:
    first = make_student_leave_attachments(backend_conn)

    with pg_error(backend_conn, UNIQUE_VIOLATION) as err:
        make_student_leave_attachments(
            backend_conn, leave_id=first["leave_id"], storage_path=first["storage_path"]
        )
    assert err.constraint_name == "uq_student_leave_attachments_path"


def test_student_leave_attachments_path_format(backend_conn: Conn) -> None:
    leave_id = make_student_leaves(backend_conn)["id"]
    good_name = uuid4().hex
    bad_paths = (
        "../etc/passwd",
        "leave-attachments/x.pdf",
        f"{leave_id}/{uuid4()}.pdf",  # 帶連字號的 36 碼 uuid
        f"{leave_id}/{good_name.upper()}.pdf",
        f"{leave_id}/{good_name}.exe",
        f"{leave_id}/{good_name}.pdf/extra",
    )
    for bad_path in bad_paths:
        with pg_error(backend_conn, CHECK_VIOLATION):
            make_student_leave_attachments(backend_conn, leave_id=leave_id, storage_path=bad_path)

    good_path = f"{leave_id}/{good_name}.pdf"
    row = make_student_leave_attachments(backend_conn, leave_id=leave_id, storage_path=good_path)
    assert row["storage_path"] == good_path


def test_student_leave_attachments_mime_whitelist(backend_conn: Conn) -> None:
    with pg_error(backend_conn, CHECK_VIOLATION):
        make_student_leave_attachments(backend_conn, mime_type="application/x-msdownload")

    for mime_type in ("image/jpeg", "image/png", "image/webp", "image/heic", "application/pdf"):
        assert make_student_leave_attachments(backend_conn, mime_type=mime_type)["mime_type"]


def test_student_leave_attachments_size_limit(backend_conn: Conn) -> None:
    for bad_size in (0, MAX_SIZE + 1):
        with pg_error(backend_conn, CHECK_VIOLATION):
            make_student_leave_attachments(backend_conn, size_bytes=bad_size)

    assert make_student_leave_attachments(backend_conn, size_bytes=MAX_SIZE)["size_bytes"] == (
        MAX_SIZE
    )


def test_student_leave_attachments_cascade(backend_conn: Conn) -> None:
    row = make_student_leave_attachments(backend_conn)

    backend_conn.execute("delete from public.student_leaves where id = %s", (row["leave_id"],))

    remaining = backend_conn.execute(
        "select count(*) from public.student_leave_attachments where id = %s", (row["id"],)
    ).fetchone()
    assert remaining == (0,)


def test_student_leave_attachments_grants(owner_conn: Conn, backend_conn: Conn) -> None:
    assert_backend_grants(owner_conn, "public.student_leave_attachments")

    row = make_student_leave_attachments(backend_conn)
    assert_backend_read_write(
        backend_conn, "public.student_leave_attachments", row, size_bytes=1024
    )


def test_student_leave_attachments_updated_at_trigger(backend_conn: Conn) -> None:
    row = make_student_leave_attachments(backend_conn, updated_at=SEEDED_UPDATED_AT)

    assert_updated_at_trigger(
        backend_conn, "public.student_leave_attachments", row["id"], size_bytes=1024
    )
