"""create_homework_items：homework_items（學生每日作業項目，domain_spec M6；DB-021）。

新功能，ivy 無對應。同一學生同一天允許同名項目（例如兩份「國語習作」），DB 不加 unique；
overall_status 的推導在 BACKEND（DB-022 只存結果）。

Revision ID: db021
Revises: db018
Create Date: 2026-10-04 16:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "db021"
down_revision: str | Sequence[str] | None = "db018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


STATEMENTS = (
    """
    create table public.homework_items (
        id uuid primary key default gen_random_uuid(),
        student_id uuid not null references public.students (id) on delete restrict,
        service_date date not null,
        subject_id uuid null references public.subjects (id) on delete set null,
        -- 例如「數學習作 p.12-13」
        title text not null,
        status text not null default 'todo',
        sort_order integer not null default 0,
        updated_by uuid null references public.staff_users (id) on delete set null,
        created_at timestamptz not null default now(),
        updated_at timestamptz not null default now(),
        check (length(btrim(title)) between 1 and 100),
        check (status in ('todo', 'doing', 'correcting', 'done'))
    )
    """,
    # 看板依日期撈全部學生
    """
    create index ix_homework_items_date_student
    on public.homework_items (service_date, student_id, sort_order)
    """,
    # FK 欄位索引（家長端依學生 + 日期）
    """
    create index ix_homework_items_student_id_service_date
    on public.homework_items (student_id, service_date)
    """,
    """
    create index ix_homework_items_subject_id on public.homework_items (subject_id)
    where subject_id is not null
    """,
    """
    create index ix_homework_items_updated_by on public.homework_items (updated_by)
    where updated_by is not null
    """,
    """
    create trigger trg_homework_items_updated_at before update on public.homework_items
    for each row execute function public.set_updated_at()
    """,
    "call app_private.grant_backend('public.homework_items')",
)


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise NotImplementedError("forward-only：以新的 revision 修正，不回滾")
