"""create_parent_accounts：parent_accounts（家長帳號，domain_spec M3；DB-015）。

對應 ivy User 中 role='parent' 的部分（line_user_id 全域唯一、display_name、token_version），拆成
獨立表；去掉 tenant_id、username / password（家長只走 LINE LIFF 登入）與 LINE 推播同意、好友狀態
欄位。家長與學生的關係由 DB-016 guardians.parent_account_id 表示。

Revision ID: db015
Revises: db014
Create Date: 2026-10-04 13:40:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "db015"
down_revision: str | Sequence[str] | None = "db014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


STATEMENTS = (
    """
    create table public.parent_accounts (
        id uuid primary key default gen_random_uuid(),
        line_user_id text not null,
        display_name text null,
        picture_url text null,
        phone text null,
        status text not null default 'active',
        token_version integer not null default 0,
        last_login_at timestamptz null,
        created_at timestamptz not null default now(),
        updated_at timestamptz not null default now(),
        constraint uq_parent_accounts_line_user_id unique (line_user_id),
        -- LINE userId 格式
        check (line_user_id ~ '^U[0-9a-f]{32}$'),
        check (display_name is null or length(display_name) <= 100),
        check (status in ('active', 'disabled')),
        check (token_version >= 0)
    )
    """,
    "create index ix_parent_accounts_status on public.parent_accounts (status)",
    """
    create trigger trg_parent_accounts_updated_at before update on public.parent_accounts
    for each row execute function public.set_updated_at()
    """,
    "call app_private.grant_backend('public.parent_accounts')",
)


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise NotImplementedError("forward-only：以新的 revision 修正，不回滾")
