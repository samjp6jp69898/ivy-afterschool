"""create_subjects：subjects（科目，domain_spec M2；DB-008；後台「參考資料」頁可增刪）。

被 homework_items（set null）、exam_subjects / exam_scores（restrict）引用；有引用時後台「刪除」
由後端改成停用（is_active = false）。預設科目由 DB-036 的 data migration seed，以 uq_subjects_name
的運算式作為 on conflict 目標。

Revision ID: db008
Revises: db003
Create Date: 2026-10-03 23:27:11.086893
"""

from collections.abc import Sequence

from alembic import op

revision: str = "db008"
down_revision: str | Sequence[str] | None = "db003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


STATEMENTS = (
    """
    create table public.subjects (
        id uuid primary key default gen_random_uuid(),
        name text not null,
        sort_order integer not null default 0,
        is_active boolean not null default true,
        created_at timestamptz not null default now(),
        updated_at timestamptz not null default now(),
        check (length(btrim(name)) between 1 and 20)
    )
    """,
    # 名稱不分大小寫、去頭尾空白唯一
    "create unique index uq_subjects_name on public.subjects (lower(btrim(name)))",
    "create index ix_subjects_sort on public.subjects (is_active, sort_order)",
    """
    create trigger trg_subjects_updated_at before update on public.subjects
    for each row execute function public.set_updated_at()
    """,
    "call app_private.grant_backend('public.subjects')",
)


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise NotImplementedError("forward-only：以新的 revision 修正，不回滾")
