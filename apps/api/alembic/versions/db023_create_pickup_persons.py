"""create_pickup_persons：pickup_persons（家長維護的常用接送人，domain_spec M7；DB-023）。

移植 ivy StudentPickupPerson：保留多孩家庭同一人需在每個孩子下各建一筆、照片私有存放、軟刪除；
person_* 欄位去前綴、is_active + deleted_at 合併為 archived_at、created_by_user_id 改為
created_by_parent_id；去掉 note、pii_redacted_at。photo_path 為 R2 物件路徑（不含
`pickup-person-photos/` 前綴，格式見 BACKEND-013 build_object_path）。

Revision ID: db023
Revises: db022
Create Date: 2026-10-04 16:40:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "db023"
down_revision: str | Sequence[str] | None = "db022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


STATEMENTS = (
    """
    create table public.pickup_persons (
        id uuid primary key default gen_random_uuid(),
        student_id uuid not null references public.students (id) on delete restrict,
        name text not null,
        -- 自由文字，例如「阿姨」「鄰居」
        relation text not null,
        phone text not null,
        photo_path text null,
        created_by_parent_id uuid null references public.parent_accounts (id) on delete set null,
        archived_at timestamptz null,
        created_at timestamptz not null default now(),
        updated_at timestamptz not null default now(),
        check (length(btrim(name)) between 1 and 50),
        check (length(btrim(relation)) between 1 and 20),
        check (phone ~ '^[0-9+\\-() ]{8,20}$')
    )
    """,
    """
    create index ix_pickup_persons_student on public.pickup_persons (student_id)
    where archived_at is null
    """,
    """
    create index ix_pickup_persons_created_by_parent_id
    on public.pickup_persons (created_by_parent_id) where created_by_parent_id is not null
    """,
    """
    create trigger trg_pickup_persons_updated_at before update on public.pickup_persons
    for each row execute function public.set_updated_at()
    """,
    "call app_private.grant_backend('public.pickup_persons')",
)


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise NotImplementedError("forward-only：以新的 revision 修正，不回滾")
