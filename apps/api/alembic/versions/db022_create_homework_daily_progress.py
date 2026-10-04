"""create_homework_daily_progress：homework_daily_progress（每日作業進度與預計接送時間；DB-022）。

domain_spec M6，新功能，ivy 無對應。ready_eta 是 Asia/Taipei 當地時間（不含日期，日期即
service_date）；BACKEND 以 `on conflict (student_id, service_date) do update` upsert。

Revision ID: db022
Revises: db021
Create Date: 2026-10-04 16:20:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "db022"
down_revision: str | Sequence[str] | None = "db021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


STATEMENTS = (
    """
    create table public.homework_daily_progress (
        id uuid primary key default gen_random_uuid(),
        student_id uuid not null references public.students (id) on delete restrict,
        service_date date not null,
        overall_status text not null default 'not_started',
        ready_eta time null,
        eta_updated_by uuid null references public.staff_users (id) on delete set null,
        eta_updated_at timestamptz null,
        -- 給家長看的說明
        note text null,
        created_at timestamptz not null default now(),
        updated_at timestamptz not null default now(),
        constraint uq_homework_daily_progress_student_date unique (student_id, service_date),
        check (overall_status in ('not_started', 'in_progress', 'done')),
        constraint ck_homework_daily_progress_eta_audit check (
            ready_eta is null or eta_updated_at is not null
        ),
        check (note is null or length(note) <= 200)
    )
    """,
    # 看板與儀表板完成率
    """
    create index ix_homework_daily_progress_date_status
    on public.homework_daily_progress (service_date, overall_status)
    """,
    """
    create index ix_homework_daily_progress_eta_updated_by
    on public.homework_daily_progress (eta_updated_by) where eta_updated_by is not null
    """,
    """
    create trigger trg_homework_daily_progress_updated_at
    before update on public.homework_daily_progress
    for each row execute function public.set_updated_at()
    """,
    "call app_private.grant_backend('public.homework_daily_progress')",
)


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise NotImplementedError("forward-only：以新的 revision 修正，不回滾")
