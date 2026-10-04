"""create_system_settings：system_settings（營運參數 key / value，domain_spec M2；DB-007）。

移植 ivy SystemConfig：config_value text 改為 value jsonb（每個 key 的 value 都是物件，schema 由
BACKEND settings_registry 驗證），加 is_secret、updated_by，去掉 tenant_id、config_type。
is_secret 列的敏感欄位由後端以應用層對稱加密後存成 base64 字串，
DB 不做加解密。預設資料由 DB-038 seed。

Revision ID: db007
Revises: db037
Create Date: 2026-10-04 15:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "db007"
down_revision: str | Sequence[str] | None = "db037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


STATEMENTS = (
    """
    create table public.system_settings (
        id uuid primary key default gen_random_uuid(),
        key text not null,
        value jsonb not null,
        is_secret boolean not null default false,
        updated_by uuid null references public.staff_users (id) on delete set null,
        created_at timestamptz not null default now(),
        updated_at timestamptz not null default now(),
        constraint uq_system_settings_key unique (key),
        -- 例如 org.profile、line.messaging
        check (key ~ '^[a-z]+(\\.[a-z_]+)+$'),
        check (jsonb_typeof(value) = 'object')
    )
    """,
    """
    create index ix_system_settings_updated_by on public.system_settings (updated_by)
    where updated_by is not null
    """,
    """
    create trigger trg_system_settings_updated_at before update on public.system_settings
    for each row execute function public.set_updated_at()
    """,
    "call app_private.grant_backend('public.system_settings')",
)


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise NotImplementedError("forward-only：以新的 revision 修正，不回滾")
