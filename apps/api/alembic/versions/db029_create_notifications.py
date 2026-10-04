"""create_notifications：notifications（站內通知收件匣，domain_spec M9；DB-029）。

參考 ivy NotificationLog：保留「一列 = 一位收件人的一個事件」、title / body 預渲染、payload jsonb、
未讀 partial index；去掉 tenant_id、channels_* 狀態欄位（LINE 外送狀態由 DB-030 outbox 追蹤；ws 為
commit 後直接廣播，不落 DB）、line_retry_*、is_inbox_visible、deep_link、source_entity。
recipient_type / recipient_id 是多型參照（staff_users / parent_accounts），不建 FK。

event 值域與 BACKEND-201 app/notifications/events.py 的 13 個事件一致；新增事件時以新 migration
替換 ck_notifications_event。

Revision ID: db029
Revises: db015
Create Date: 2026-10-04 13:50:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "db029"
down_revision: str | Sequence[str] | None = "db015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


STATEMENTS = (
    """
    create table public.notifications (
        id uuid primary key default gen_random_uuid(),
        recipient_type text not null,
        recipient_id uuid not null,
        event text not null,
        title text not null,
        body text not null,
        payload jsonb not null default '{}',
        read_at timestamptz null,
        created_at timestamptz not null default now(),
        updated_at timestamptz not null default now(),
        check (recipient_type in ('staff', 'parent')),
        constraint ck_notifications_event check (
            event in (
                'attendance.checked_in',
                'attendance.checked_out',
                'leave.created',
                'leave.cancelled',
                'homework.eta_updated',
                'homework.done',
                'pickup.requested',
                'pickup.replied',
                'pickup.arrived',
                'pickup.completed',
                'pickup.cancelled',
                'exam.published',
                'binding.completed'
            )
        ),
        check (length(title) between 1 and 100),
        check (length(body) <= 1000),
        check (jsonb_typeof(payload) = 'object')
    )
    """,
    # 收件匣列表
    """
    create index ix_notifications_recipient_created
    on public.notifications (recipient_type, recipient_id, created_at desc)
    """,
    # 未讀數 / 全部已讀
    """
    create index ix_notifications_recipient_unread
    on public.notifications (recipient_type, recipient_id) where read_at is null
    """,
    """
    create trigger trg_notifications_updated_at before update on public.notifications
    for each row execute function public.set_updated_at()
    """,
    "call app_private.grant_backend('public.notifications')",
)


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise NotImplementedError("forward-only：以新的 revision 修正，不回滾")
