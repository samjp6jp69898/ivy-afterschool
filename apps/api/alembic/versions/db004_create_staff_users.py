"""create_staff_users：staff_users（員工帳號，domain_spec M1；DB-004）。

移植 ivy User 的 token_version、must_change_password、is_active 語意；去掉 tenant_id、employee_id
與家長欄位（家長改用 DB-015 parent_accounts）。ivy 的 permission_names 改為 extra_permissions /
revoked_permissions 兩個陣列，在角色權限之外個別加減；萬用碼 `*` 只能經 admin 角色取得。

Revision ID: db004
Revises: db012
Create Date: 2026-10-04 13:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "db004"
down_revision: str | Sequence[str] | None = "db012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


STATEMENTS = (
    """
    create table public.staff_users (
        id uuid primary key default gen_random_uuid(),
        username text not null,
        password_hash text not null,
        display_name text not null,
        phone text null,
        email text null,
        role_id uuid not null references public.roles (id) on delete restrict,
        extra_permissions text[] not null default '{}',
        revoked_permissions text[] not null default '{}',
        is_active boolean not null default true,
        token_version integer not null default 0,
        last_login_at timestamptz null,
        must_change_password boolean not null default true,
        created_at timestamptz not null default now(),
        updated_at timestamptz not null default now(),
        constraint uq_staff_users_username unique (username),
        constraint ck_staff_users_username_format check (
            username = lower(username) and username ~ '^[a-z0-9][a-z0-9._-]{2,31}$'
        ),
        -- 只接受 argon2id 編碼字串，防止誤存明文
        constraint ck_staff_users_password_hash_argon2id
            check (starts_with(password_hash, '$argon2id$')),
        check (length(btrim(display_name)) between 1 and 50),
        check (email is null or email ~ '^[^@\\s]+@[^@\\s]+$'),
        constraint ck_staff_users_no_wildcard check (not ('*' = any (extra_permissions))),
        check (token_version >= 0)
    )
    """,
    "create index ix_staff_users_role_id on public.staff_users (role_id)",
    "create index ix_staff_users_active on public.staff_users (is_active)",
    """
    create trigger trg_staff_users_updated_at before update on public.staff_users
    for each row execute function public.set_updated_at()
    """,
    "call app_private.grant_backend('public.staff_users')",
)


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise NotImplementedError("forward-only：以新的 revision 修正，不回滾")
