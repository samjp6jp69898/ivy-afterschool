"""create_pickup_authorizations：pickup_authorizations（單日代理接送授權，domain_spec M7；DB-024）。

移植 ivy PickupAuthorization：保留接送人資訊快照、pickup_person_id 可空（一次性接送人）、
status 三態、核銷方式；可逆加密 code_encrypted 改為 code_hash + code_last4（員工端核對完整碼、
只顯示末四碼）；保留防窮舉欄位 code_attempts / code_locked_at；去掉 tenant_id、batch_key、
dismissal_call_id、override_note、pii_redacted_at、relation / photo 快照。

code_locked_at 於連錯第 5 次時寫入，不自動解鎖，鎖定後只能由有 pickup:override 的員工完成。
BACKEND 驗碼失敗以單一語句原子累計 code_attempts，避免併發驗碼繞過上限。

Revision ID: db024
Revises: db020
Create Date: 2026-10-04 19:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "db024"
down_revision: str | Sequence[str] | None = "db020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


STATEMENTS = (
    """
    create table public.pickup_authorizations (
        id uuid primary key default gen_random_uuid(),
        student_id uuid not null references public.students (id) on delete restrict,
        service_date date not null,
        pickup_person_id uuid null references public.pickup_persons (id) on delete set null,
        proxy_name text not null,
        proxy_phone text not null,
        -- 6 位數字接送碼的 HMAC-SHA256(APP_SECRET_KEY, code) hex，BACKEND 計算
        code_hash text not null,
        code_last4 text not null,
        code_attempts integer not null default 0,
        code_locked_at timestamptz null,
        status text not null default 'active',
        verified_at timestamptz null,
        verified_by uuid null references public.staff_users (id) on delete set null,
        verification_method text null,
        created_by_parent_id uuid null references public.parent_accounts (id) on delete set null,
        created_at timestamptz not null default now(),
        updated_at timestamptz not null default now(),
        check (length(btrim(proxy_name)) between 1 and 50),
        check (proxy_phone ~ '^[0-9+\\-() ]{8,20}$'),
        check (code_hash ~ '^[0-9a-f]{64}$'),
        check (code_last4 ~ '^[0-9]{4}$'),
        check (code_attempts between 0 and 5),
        constraint ck_pickup_authorizations_lock check (
            (code_locked_at is not null) = (code_attempts = 5)
        ),
        check (status in ('active', 'completed', 'cancelled')),
        check (verification_method in ('code', 'visual_match', 'override')),
        constraint ck_pickup_authorizations_verified check (
            (status = 'completed') = (verified_at is not null)
            and (verified_at is null) = (verification_method is null)
        )
    )
    """,
    """
    create index ix_pickup_authorizations_student_date
    on public.pickup_authorizations (student_id, service_date)
    """,
    # 員工端代理核驗清單
    """
    create index ix_pickup_authorizations_date_status
    on public.pickup_authorizations (service_date, status)
    """,
    """
    create index ix_pickup_authorizations_pickup_person_id
    on public.pickup_authorizations (pickup_person_id) where pickup_person_id is not null
    """,
    """
    create index ix_pickup_authorizations_verified_by
    on public.pickup_authorizations (verified_by) where verified_by is not null
    """,
    """
    create index ix_pickup_authorizations_created_by_parent_id
    on public.pickup_authorizations (created_by_parent_id)
    where created_by_parent_id is not null
    """,
    """
    create trigger trg_pickup_authorizations_updated_at
    before update on public.pickup_authorizations
    for each row execute function public.set_updated_at()
    """,
    "call app_private.grant_backend('public.pickup_authorizations')",
)


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise NotImplementedError("forward-only：以新的 revision 修正，不回滾")
