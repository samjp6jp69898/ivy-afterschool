"""create_guardians：guardians（學生監護人 / 聯絡人，domain_spec M3；DB-016）。

移植 ivy Guardian：保留同一學生至多一位 is_primary、can_pickup、軟刪除、一個家長帳號可被多筆
guardian 引用（一家長綁多孩）；去掉 email、is_emergency、custody_note、sort_order、pii_redacted_at；
user_id 改為 parent_account_id、deleted_at 改名 archived_at；relation 改為值域。

Revision ID: db016
Revises: db013
Create Date: 2026-10-04 15:20:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "db016"
down_revision: str | Sequence[str] | None = "db013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


STATEMENTS = (
    """
    create table public.guardians (
        id uuid primary key default gen_random_uuid(),
        student_id uuid not null references public.students (id) on delete restrict,
        -- 綁定 LINE 後填入
        parent_account_id uuid null references public.parent_accounts (id) on delete set null,
        name text not null,
        relation text not null,
        phone text null,
        is_primary boolean not null default false,
        can_pickup boolean not null default true,
        receives_notifications boolean not null default true,
        archived_at timestamptz null,
        created_at timestamptz not null default now(),
        updated_at timestamptz not null default now(),
        check (length(btrim(name)) between 1 and 50),
        check (relation in ('father', 'mother', 'grandparent', 'other'))
    )
    """,
    # 同一學生未封存的 guardian 只能一位 is_primary
    """
    create unique index uq_guardians_one_primary on public.guardians (student_id)
    where is_primary and archived_at is null
    """,
    # 同一家長帳號不會對同一學生綁兩次
    """
    create unique index uq_guardians_student_parent
    on public.guardians (student_id, parent_account_id)
    where parent_account_id is not null and archived_at is null
    """,
    "create index ix_guardians_student on public.guardians (student_id) where archived_at is null",
    # 家長可見範圍查詢（_get_parent_student_ids）
    """
    create index ix_guardians_parent_account on public.guardians (parent_account_id)
    where parent_account_id is not null
    """,
    "create index ix_guardians_phone on public.guardians (phone)",
    """
    create trigger trg_guardians_updated_at before update on public.guardians
    for each row execute function public.set_updated_at()
    """,
    "call app_private.grant_backend('public.guardians')",
)


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise NotImplementedError("forward-only：以新的 revision 修正，不回滾")
