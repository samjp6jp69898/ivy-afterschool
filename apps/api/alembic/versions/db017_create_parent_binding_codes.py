"""create_parent_binding_codes：parent_binding_codes（家長綁定碼，domain_spec M3；DB-017）。

移植 ivy GuardianBindingCode：保留 code_hash 唯一、used_at 非 null 即視為已用、created_by 稽核；
去掉 tenant_id、used_by_user_id（綁定結果寫在 guardians.parent_account_id）。明碼為 8 碼英數、只顯示
一次；hash 由 BACKEND 以 HMAC-SHA256(APP_SECRET_KEY, 正規化大寫明碼) 計算。

Revision ID: db017
Revises: db026
Create Date: 2026-10-04 18:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "db017"
down_revision: str | Sequence[str] | None = "db026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


STATEMENTS = (
    """
    create table public.parent_binding_codes (
        id uuid primary key default gen_random_uuid(),
        guardian_id uuid not null references public.guardians (id) on delete cascade,
        code_hash text not null,
        expires_at timestamptz not null,
        used_at timestamptz null,
        created_by uuid not null references public.staff_users (id) on delete restrict,
        created_at timestamptz not null default now(),
        updated_at timestamptz not null default now(),
        constraint uq_parent_binding_codes_code_hash unique (code_hash),
        check (code_hash ~ '^[0-9a-f]{64}$'),
        check (expires_at > created_at)
    )
    """,
    # 重新產碼時作廢舊碼
    """
    create index ix_parent_binding_codes_guardian_unused
    on public.parent_binding_codes (guardian_id)
    where used_at is null
    """,
    """
    create index ix_parent_binding_codes_expires on public.parent_binding_codes (expires_at)
    where used_at is null
    """,
    "create index ix_parent_binding_codes_created_by on public.parent_binding_codes (created_by)",
    """
    create trigger trg_parent_binding_codes_updated_at before update on public.parent_binding_codes
    for each row execute function public.set_updated_at()
    """,
    "call app_private.grant_backend('public.parent_binding_codes')",
)


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise NotImplementedError("forward-only：以新的 revision 修正，不回滾")
