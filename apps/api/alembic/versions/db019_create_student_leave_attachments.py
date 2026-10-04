"""create_student_leave_attachments：student_leave_attachments（請假附件 metadata，M5；DB-019）。

檔案本體存在 R2 私有 bucket 的 `leave-attachments/` 前綴下（BACKEND-013 R2Storage），後端上傳並簽發
短效 presigned URL；本表只存物件路徑與檔案資訊。storage_path 格式與 BACKEND-013 build_object_path
一致：`<leave_id>/<uuid4 32 碼小寫 hex>.<ext>`，不含 key 前綴、不含使用者提供的檔名。

Revision ID: db019
Revises: db017
Create Date: 2026-10-04 18:20:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "db019"
down_revision: str | Sequence[str] | None = "db017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


STATEMENTS = (
    """
    create table public.student_leave_attachments (
        id uuid primary key default gen_random_uuid(),
        leave_id uuid not null references public.student_leaves (id) on delete cascade,
        storage_path text not null,
        mime_type text not null,
        size_bytes integer not null,
        created_at timestamptz not null default now(),
        updated_at timestamptz not null default now(),
        constraint uq_student_leave_attachments_path unique (storage_path),
        check (storage_path ~ '^[0-9a-f-]{36}/[0-9a-f]{32}\\.(jpg|png|webp|heic|pdf)$'),
        check (mime_type in (
            'image/jpeg', 'image/png', 'image/webp', 'image/heic', 'application/pdf'
        )),
        -- 10 MB，與 BACKEND-016 的附件大小上限一致
        check (size_bytes > 0 and size_bytes <= 10485760)
    )
    """,
    """
    create index ix_student_leave_attachments_leave
    on public.student_leave_attachments (leave_id)
    """,
    """
    create trigger trg_student_leave_attachments_updated_at
    before update on public.student_leave_attachments
    for each row execute function public.set_updated_at()
    """,
    "call app_private.grant_backend('public.student_leave_attachments')",
)


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise NotImplementedError("forward-only：以新的 revision 修正，不回滾")
