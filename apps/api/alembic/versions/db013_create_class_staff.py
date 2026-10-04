"""create_class_staff：class_staff（班級負責員工，domain_spec M3；DB-013）。

取代 ivy Classroom.head_teacher_id / assistant_teacher_id 的固定欄位，改為多對多；僅作為後台「我的班」
篩選與請假通知收件人，不是教師端。同一員工在同一班只有一個角色；一班可有多位 lead，DB 不限制。

Revision ID: db013
Revises: db007
Create Date: 2026-10-04 15:10:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "db013"
down_revision: str | Sequence[str] | None = "db007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


STATEMENTS = (
    """
    create table public.class_staff (
        id uuid primary key default gen_random_uuid(),
        class_id uuid not null references public.classes (id) on delete cascade,
        staff_user_id uuid not null references public.staff_users (id) on delete cascade,
        role text not null default 'assistant',
        created_at timestamptz not null default now(),
        updated_at timestamptz not null default now(),
        constraint uq_class_staff_class_staff unique (class_id, staff_user_id),
        check (role in ('lead', 'assistant'))
    )
    """,
    # 「我的班」與通知收件人查詢（class_id 由 uq_class_staff_class_staff 的前導欄位涵蓋）
    "create index ix_class_staff_staff_user on public.class_staff (staff_user_id)",
    """
    create trigger trg_class_staff_updated_at before update on public.class_staff
    for each row execute function public.set_updated_at()
    """,
    "call app_private.grant_backend('public.class_staff')",
)


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise NotImplementedError("forward-only：以新的 revision 修正，不回滾")
