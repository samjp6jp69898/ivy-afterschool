"""create_pickup_requests：pickup_requests（家長「我要來接」接送請求，domain_spec M7；DB-025）。

移植 ivy StudentDismissalCall：保留 status / source CHECK、「學生 + 進行中狀態」索引、
expected_arrival_at / arrived_at / cancelled_at；ivy 的 pending / acknowledged / completed /
cancelled 擴充為六態（加 arrived、expired），新增自動回覆欄位、完成方式與接走人；去掉 tenant_id、
classroom_id、bus 來源、client_request_id（同學生同日非終態唯一已防重複送出）。

BACKEND 建立請求遇到 23505（uq_pickup_requests_one_open）要轉成 409 業務錯誤；狀態轉換的合法性由
BACKEND 守，DB 只保證欄位一致性。

Revision ID: db025
Revises: db038
Create Date: 2026-10-04 21:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "db025"
down_revision: str | Sequence[str] | None = "db038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


STATEMENTS = (
    """
    create table public.pickup_requests (
        id uuid primary key default gen_random_uuid(),
        student_id uuid not null references public.students (id) on delete restrict,
        service_date date not null,
        source text not null,
        requested_by_type text not null,
        -- 多型參照 parent_accounts / staff_users，不建 FK
        requested_by_id uuid not null,
        expected_arrival_at timestamptz null,
        status text not null default 'pending',
        homework_status_at_request text null,
        reply_ready_eta time null,
        reply_message text null,
        reply_source text null,
        replied_at timestamptz null,
        replied_by uuid null references public.staff_users (id) on delete set null,
        arrived_at timestamptz null,
        completed_at timestamptz null,
        completed_by uuid null references public.staff_users (id) on delete set null,
        picked_up_by_guardian_id uuid null references public.guardians (id) on delete restrict,
        picked_up_by_authorization_id uuid null
            references public.pickup_authorizations (id) on delete restrict,
        completion_method text null,
        cancelled_at timestamptz null,
        cancel_reason text null,
        created_at timestamptz not null default now(),
        updated_at timestamptz not null default now(),
        check (source in ('parent', 'staff', 'proxy')),
        check (requested_by_type in ('parent', 'staff')),
        check (status in (
            'pending', 'acknowledged', 'arrived', 'completed', 'cancelled', 'expired'
        )),
        check (homework_status_at_request in ('not_started', 'in_progress', 'done')),
        check (length(reply_message) <= 200),
        check (reply_source in ('auto', 'staff')),
        constraint ck_pickup_requests_reply check (
            (reply_source is null) = (replied_at is null)
            and (reply_source <> 'staff' or replied_by is not null)
            and (reply_source <> 'auto' or replied_by is null)
        ),
        check (completion_method in ('guardian', 'code', 'visual_match', 'override')),
        constraint ck_pickup_requests_completed check (
            (status = 'completed') = (completed_at is not null)
            and (completed_at is null) = (completion_method is null)
        ),
        constraint ck_pickup_requests_completion_target check (
            (completion_method <> 'guardian' or picked_up_by_guardian_id is not null)
            and (
                completion_method not in ('code', 'visual_match')
                or picked_up_by_authorization_id is not null
            )
            and not (
                picked_up_by_guardian_id is not null and picked_up_by_authorization_id is not null
            )
        ),
        constraint ck_pickup_requests_arrived check (status <> 'arrived' or arrived_at is not null),
        check (length(cancel_reason) <= 200),
        constraint ck_pickup_requests_cancelled check (
            (status = 'cancelled') = (cancelled_at is not null)
        )
    )
    """,
    # 同一學生同一天只能有一筆非終態請求（終態為 completed / cancelled / expired）
    """
    create unique index uq_pickup_requests_one_open
    on public.pickup_requests (student_id, service_date)
    where status in ('pending', 'acknowledged', 'arrived')
    """,
    # 接送佇列
    "create index ix_pickup_requests_date_status on public.pickup_requests (service_date, status)",
    # 自動過期背景工作
    """
    create index ix_pickup_requests_open_created on public.pickup_requests (created_at)
    where status in ('pending', 'acknowledged', 'arrived')
    """,
    """
    create index ix_pickup_requests_student_id_service_date
    on public.pickup_requests (student_id, service_date)
    """,
    """
    create index ix_pickup_requests_replied_by on public.pickup_requests (replied_by)
    where replied_by is not null
    """,
    """
    create index ix_pickup_requests_completed_by on public.pickup_requests (completed_by)
    where completed_by is not null
    """,
    """
    create index ix_pickup_requests_picked_up_by_guardian_id
    on public.pickup_requests (picked_up_by_guardian_id) where picked_up_by_guardian_id is not null
    """,
    """
    create index ix_pickup_requests_picked_up_by_authorization_id
    on public.pickup_requests (picked_up_by_authorization_id)
    where picked_up_by_authorization_id is not null
    """,
    """
    create trigger trg_pickup_requests_updated_at before update on public.pickup_requests
    for each row execute function public.set_updated_at()
    """,
    "call app_private.grant_backend('public.pickup_requests')",
)


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise NotImplementedError("forward-only：以新的 revision 修正，不回滾")
