"""create_exams：exams（考試，domain_spec M8；DB-026）。

新功能，ivy 無對應。grade_level 與 class_id 至少一個，決定應考學生名單；取消發布時 BACKEND 同時
清空 published_at / published_by。

Revision ID: db026
Revises: db023
Create Date: 2026-10-04 17:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "db026"
down_revision: str | Sequence[str] | None = "db023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


STATEMENTS = (
    """
    create table public.exams (
        id uuid primary key default gen_random_uuid(),
        -- 例如「第一次段考」
        name text not null,
        exam_type_id uuid not null references public.exam_types (id) on delete restrict,
        exam_date date not null,
        grade_level integer null,
        class_id uuid null references public.classes (id) on delete restrict,
        status text not null default 'draft',
        published_at timestamptz null,
        published_by uuid null references public.staff_users (id) on delete set null,
        note text null,
        created_at timestamptz not null default now(),
        updated_at timestamptz not null default now(),
        check (length(btrim(name)) between 1 and 50),
        check (grade_level between 1 and 6),
        constraint ck_exams_scope check (grade_level is not null or class_id is not null),
        check (status in ('draft', 'published')),
        constraint ck_exams_published check ((status = 'published') = (published_at is not null)),
        check (note is null or length(note) <= 500)
    )
    """,
    "create index ix_exams_date on public.exams (exam_date desc)",
    # 家長端只看 published
    "create index ix_exams_status_date on public.exams (status, exam_date desc)",
    "create index ix_exams_class on public.exams (class_id) where class_id is not null",
    "create index ix_exams_grade on public.exams (grade_level) where grade_level is not null",
    "create index ix_exams_exam_type_id on public.exams (exam_type_id)",
    """
    create index ix_exams_published_by on public.exams (published_by)
    where published_by is not null
    """,
    """
    create trigger trg_exams_updated_at before update on public.exams
    for each row execute function public.set_updated_at()
    """,
    "call app_private.grant_backend('public.exams')",
)


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise NotImplementedError("forward-only：以新的 revision 修正，不回滾")
