"""create_exam_subjects：exam_subjects（考試的應考科目與滿分，domain_spec M8；DB-027）。

新功能，ivy 無對應。(exam_id, subject_id) 的 unique 同時是 DB-028 exam_scores 複合 FK 的參照鍵。
full_score 下修保護的 trigger function 在 DB-028 的 migration 內建立（exam_scores 屆時才存在）。

Revision ID: db027
Revises: db024
Create Date: 2026-10-04 19:20:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "db027"
down_revision: str | Sequence[str] | None = "db024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


STATEMENTS = (
    """
    create table public.exam_subjects (
        id uuid primary key default gen_random_uuid(),
        exam_id uuid not null references public.exams (id) on delete cascade,
        subject_id uuid not null references public.subjects (id) on delete restrict,
        full_score numeric(6, 2) not null default 100,
        sort_order integer not null default 0,
        created_at timestamptz not null default now(),
        updated_at timestamptz not null default now(),
        constraint uq_exam_subjects_exam_subject unique (exam_id, subject_id),
        check (full_score > 0 and full_score <= 1000)
    )
    """,
    "create index ix_exam_subjects_subject_id on public.exam_subjects (subject_id)",
    """
    create trigger trg_exam_subjects_updated_at before update on public.exam_subjects
    for each row execute function public.set_updated_at()
    """,
    "call app_private.grant_backend('public.exam_subjects')",
)


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise NotImplementedError("forward-only：以新的 revision 修正，不回滾")
