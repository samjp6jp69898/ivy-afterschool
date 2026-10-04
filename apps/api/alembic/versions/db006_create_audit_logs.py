"""create_audit_logs：audit_logs（稽核紀錄，domain_spec M1；DB-006），append-only。

移植 ivy AuditLog 的 entity_type / entity_id / before-after 結構；去掉 tenant_id、ack 欄位、
session_id、impersonated_by；ivy 的 changes text 拆成 before / after jsonb。actor_id 是多型參照，
不建 FK，避免稽核寫入被參照完整性擋下。

append-only：app_backend 只有 select / insert。grant_backend 只收回 PUBLIC、不收回 app_backend
既有的權限，所以先明確 revoke app_backend 的全部權限再授權，確保結果與執行前的 ACL 無關。
set_updated_at trigger 照共用欄位慣例掛上（後端無 update 權限，實際上不會觸發）。

Revision ID: db006
Revises: db005
Create Date: 2026-10-04 13:20:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "db006"
down_revision: str | Sequence[str] | None = "db005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


STATEMENTS = (
    """
    create table public.audit_logs (
        id uuid primary key default gen_random_uuid(),
        actor_type text not null,
        actor_id uuid null,
        action text not null,
        entity_type text not null,
        entity_id text null,
        before jsonb null,
        after jsonb null,
        ip inet null,
        user_agent text null,
        created_at timestamptz not null default now(),
        updated_at timestamptz not null default now(),
        check (actor_type in ('staff', 'parent', 'system', 'device')),
        constraint ck_audit_logs_actor_id check (actor_type = 'system' or actor_id is not null),
        -- 例如 exam_score.update、attendance.amend、pickup.override_complete
        check (action ~ '^[a-z_]+\\.[a-z_]+$')
    )
    """,
    "create index ix_audit_logs_created_at on public.audit_logs (created_at desc)",
    "create index ix_audit_logs_entity on public.audit_logs (entity_type, entity_id)",
    """
    create index ix_audit_logs_actor on public.audit_logs (actor_type, actor_id, created_at desc)
    """,
    """
    create trigger trg_audit_logs_updated_at before update on public.audit_logs
    for each row execute function public.set_updated_at()
    """,
    "revoke all on table public.audit_logs from app_backend",
    "call app_private.grant_backend('public.audit_logs', 'select, insert')",
)


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise NotImplementedError("forward-only：以新的 revision 修正，不回滾")
