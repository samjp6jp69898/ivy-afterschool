"""create_student_leaves：student_leaves（學生請假，domain_spec M5；DB-018）。

參考 ivy StudentLeaveRequest：保留 student_id restrict、日期區間索引、status CHECK；去掉審核流程
（家長送出即生效，只有 active / cancelled）、applicant_user_id 改為多型
created_by_type / created_by_id（不建 FK）、
單一 attachment_path 改為 DB-019 附件表、去掉 tenant_id。

同一學生 active 請假的日期區間（含頭尾）以 btree_gist exclusion constraint 禁止重疊，違反時拋
exclusion_violation（23P01），BACKEND 轉成 409 leave_overlap。btree_gist 裝在 extensions schema，
uuid 的 operator class 明確寫 extensions.gist_uuid_ops，不依賴 search_path；constraint 是否可由
app_backend 寫入由 test_student_leaves 實測。

Revision ID: db018
Revises: db016
Create Date: 2026-10-04 15:40:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "db018"
down_revision: str | Sequence[str] | None = "db016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


STATEMENTS = (
    "create extension if not exists btree_gist with schema extensions",
    """
    create table public.student_leaves (
        id uuid primary key default gen_random_uuid(),
        student_id uuid not null references public.students (id) on delete restrict,
        leave_type text not null,
        start_date date not null,
        end_date date not null,
        reason text null,
        status text not null default 'active',
        -- 多型參照 parent_accounts / staff_users，不建 FK
        created_by_type text not null,
        created_by_id uuid not null,
        cancelled_at timestamptz null,
        cancelled_by_type text null,
        cancelled_by_id uuid null,
        created_at timestamptz not null default now(),
        updated_at timestamptz not null default now(),
        check (leave_type in ('sick', 'personal', 'other')),
        -- 單筆最長 61 天，防止誤輸入跨年
        constraint ck_student_leaves_range check (
            end_date >= start_date and end_date - start_date <= 60
        ),
        check (reason is null or length(reason) <= 500),
        check (status in ('active', 'cancelled')),
        check (created_by_type in ('parent', 'staff')),
        check (cancelled_by_type in ('parent', 'staff')),
        constraint ck_student_leaves_cancel_consistency check (
            (status = 'cancelled') = (cancelled_at is not null)
            and (cancelled_at is null) = (cancelled_by_type is null)
            and (cancelled_by_type is null) = (cancelled_by_id is null)
        ),
        constraint ex_student_leaves_no_overlap exclude using gist (
            student_id extensions.gist_uuid_ops with =,
            daterange(start_date, end_date, '[]') with &&
        ) where (status = 'active')
    )
    """,
    # 請假期間與出勤套用查詢
    """
    create index ix_student_leaves_student_range
    on public.student_leaves (student_id, start_date, end_date)
    """,
    # 每日出勤初始化找當日請假
    """
    create index ix_student_leaves_active_range on public.student_leaves (start_date, end_date)
    where status = 'active'
    """,
    "create index ix_student_leaves_created_at on public.student_leaves (created_at desc)",
    """
    create trigger trg_student_leaves_updated_at before update on public.student_leaves
    for each row execute function public.set_updated_at()
    """,
    "call app_private.grant_backend('public.student_leaves')",
)


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise NotImplementedError("forward-only：以新的 revision 修正，不回滾")
