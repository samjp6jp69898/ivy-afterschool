"""create_notification_outbox：notification_outbox（通知外送 outbox，domain_spec M9；DB-030）。

移植 ivy NotificationOutbox 的「commit 後才派送、失敗重試、sweeper 撿漏」語意；本專案改為每個
notification 與 channel 的組合一列，在業務 transaction 內與 notifications 一起寫入，派送器在
commit 後處理 pending。去掉 tenant_id、dedupe_token（以 unique(notification_id, channel) 取代）、
序列化事件 context、log_id。channel 只有 line：in_app 本身就是 notifications 列，ws 不進 outbox
（BACKEND 在 commit 後直接廣播）。指數退避間隔與最大次數（超過轉 dead）由 BACKEND 決定，DB 只限制
attempts 上限 20 防止失控。

Revision ID: db030
Revises: db027
Create Date: 2026-10-04 19:40:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "db030"
down_revision: str | Sequence[str] | None = "db027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


STATEMENTS = (
    """
    create table public.notification_outbox (
        id uuid primary key default gen_random_uuid(),
        notification_id uuid not null references public.notifications (id) on delete cascade,
        channel text not null default 'line',
        status text not null default 'pending',
        attempts integer not null default 0,
        next_attempt_at timestamptz not null default now(),
        last_error text null,
        created_at timestamptz not null default now(),
        updated_at timestamptz not null default now(),
        -- 同一通知同一頻道只派一次（取代 ivy dedupe_token）
        constraint uq_notification_outbox_notification_channel unique (notification_id, channel),
        check (channel in ('line')),
        check (status in ('pending', 'sent', 'failed', 'dead')),
        check (attempts >= 0 and attempts <= 20),
        check (last_error is null or length(last_error) <= 2000),
        constraint ck_notification_outbox_dead_has_error check (
            status not in ('failed', 'dead') or last_error is not null
        )
    )
    """,
    # retry scheduler / sweeper 以 select ... for update skip locked 撿工作
    """
    create index ix_notification_outbox_due on public.notification_outbox (next_attempt_at)
    where status in ('pending', 'failed')
    """,
    """
    create trigger trg_notification_outbox_updated_at before update on public.notification_outbox
    for each row execute function public.set_updated_at()
    """,
    "call app_private.grant_backend('public.notification_outbox')",
)


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise NotImplementedError("forward-only：以新的 revision 修正，不回滾")
