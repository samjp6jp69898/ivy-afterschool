"""create_refresh_tokens：refresh_tokens（員工與家長共用的 refresh token，domain_spec M1；DB-005）。

移植 ivy StaffRefreshToken / ParentRefreshToken 的 family 輪替與重用偵測，兩張合併成一張並以
subject_type 區分；ivy 的 parent_token_id 改成指向下一棒的 replaced_by（非 null = 已輪替過，再次
出現即為重用，後端撤銷整個 family）。DB 只存 sha256 hex，不存明文 token。

subject_id 是多型參照（staff_users / parent_accounts），不建 FK；帳號停用時由後端 token_version
+1 並撤銷 family。

Revision ID: db005
Revises: db004
Create Date: 2026-10-04 13:10:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "db005"
down_revision: str | Sequence[str] | None = "db004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


STATEMENTS = (
    """
    create table public.refresh_tokens (
        id uuid primary key default gen_random_uuid(),
        subject_type text not null,
        subject_id uuid not null,
        family_id uuid not null,
        token_hash text not null,
        expires_at timestamptz not null,
        revoked_at timestamptz null,
        replaced_by uuid null references public.refresh_tokens (id) on delete set null,
        created_at timestamptz not null default now(),
        updated_at timestamptz not null default now(),
        constraint uq_refresh_tokens_token_hash unique (token_hash),
        constraint ck_refresh_tokens_token_hash_format check (token_hash ~ '^[0-9a-f]{64}$'),
        check (subject_type in ('staff', 'parent')),
        check (expires_at > created_at),
        check (replaced_by is null or replaced_by <> id)
    )
    """,
    "create index ix_refresh_tokens_subject on public.refresh_tokens (subject_type, subject_id)",
    "create index ix_refresh_tokens_family on public.refresh_tokens (family_id)",
    # 過期清理
    "create index ix_refresh_tokens_expires_at on public.refresh_tokens (expires_at)",
    # FK 索引：刪除被指向的列時 set null 的反查
    """
    create index ix_refresh_tokens_replaced_by on public.refresh_tokens (replaced_by)
    where replaced_by is not null
    """,
    """
    create trigger trg_refresh_tokens_updated_at before update on public.refresh_tokens
    for each row execute function public.set_updated_at()
    """,
    "call app_private.grant_backend('public.refresh_tokens')",
)


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise NotImplementedError("forward-only：以新的 revision 修正，不回滾")
