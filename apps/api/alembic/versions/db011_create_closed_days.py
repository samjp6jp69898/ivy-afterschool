"""create_closed_days：closed_days（安親班休息日，domain_spec M2；DB-011）。

出勤每日初始化（BACKEND）跳過這些日期。uq_closed_days_date 本身即提供依日期查詢的索引，不另建。

Revision ID: db011
Revises: db010
Create Date: 2026-10-03 23:27:12.689500
"""

from collections.abc import Sequence

from alembic import op

revision: str = "db011"
down_revision: str | Sequence[str] | None = "db010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


STATEMENTS = (
    """
    create table public.closed_days (
        id uuid primary key default gen_random_uuid(),
        date date not null,
        reason text null,
        created_at timestamptz not null default now(),
        updated_at timestamptz not null default now(),
        constraint uq_closed_days_date unique (date),
        check (reason is null or length(reason) <= 100)
    )
    """,
    """
    create trigger trg_closed_days_updated_at before update on public.closed_days
    for each row execute function public.set_updated_at()
    """,
    "call app_private.grant_backend('public.closed_days')",
)


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise NotImplementedError("forward-only：以新的 revision 修正，不回滾")
