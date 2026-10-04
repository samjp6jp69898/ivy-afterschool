"""create_notification_preferences：notification_preferences（家長 LINE 推播偏好，M9；DB-031）。

移植 ivy ParentNotificationPreference 的稀疏列設計（沒有列 = 預設開啟）；去掉 tenant_id；
(user_id, event_type, channel) 三元組改為 (parent_account_id, event) + line_enabled（in_app 一律
開啟，不存偏好）。event 只收家長收件、預設頻道含 line 的事件；binding.completed 只有 in_app，
不可設定。

Revision ID: db031
Revises: db030
Create Date: 2026-10-04 20:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "db031"
down_revision: str | Sequence[str] | None = "db030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# unique 的前導欄位 parent_account_id 已涵蓋依家長查詢與 FK 索引，不另建索引
STATEMENTS = (
    """
    create table public.notification_preferences (
        id uuid primary key default gen_random_uuid(),
        parent_account_id uuid not null references public.parent_accounts (id) on delete cascade,
        event text not null,
        line_enabled boolean not null default true,
        created_at timestamptz not null default now(),
        updated_at timestamptz not null default now(),
        constraint uq_notification_preferences_parent_event unique (parent_account_id, event),
        constraint ck_notification_preferences_event check (
            event in (
                'attendance.checked_in', 'attendance.checked_out', 'homework.eta_updated',
                'homework.done', 'pickup.replied', 'pickup.completed', 'exam.published'
            )
        )
    )
    """,
    """
    create trigger trg_notification_preferences_updated_at
    before update on public.notification_preferences
    for each row execute function public.set_updated_at()
    """,
    "call app_private.grant_backend('public.notification_preferences')",
)


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise NotImplementedError("forward-only：以新的 revision 修正，不回滾")
