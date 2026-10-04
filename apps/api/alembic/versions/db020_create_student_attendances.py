"""create_student_attendances：student_attendances（學生每日出勤，domain_spec M4；DB-020）。

參考 ivy StudentAttendance：保留 unique(student_id, date)、日期索引、status CHECK；中文狀態值改為
expected / present / left / absent / leave，加到班 / 離班時間與來源、leave_id；去掉 remark 前綴機制
（改由 leave_id 關聯）。BACKEND 每日初始化以 `on conflict (student_id, service_date) do nothing`
保持冪等；請假套用 / 回復出勤要在同一 transaction 內更新 status 與 leave_id。

Revision ID: db020
Revises: db019
Create Date: 2026-10-04 18:40:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "db020"
down_revision: str | Sequence[str] | None = "db019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


STATEMENTS = (
    """
    create table public.student_attendances (
        id uuid primary key default gen_random_uuid(),
        student_id uuid not null references public.students (id) on delete restrict,
        service_date date not null,
        status text not null default 'expected',
        check_in_at timestamptz null,
        check_in_source text null,
        check_out_at timestamptz null,
        check_out_source text null,
        -- 請假只取消不硬刪
        leave_id uuid null references public.student_leaves (id) on delete restrict,
        note text null,
        updated_by uuid null references public.staff_users (id) on delete set null,
        created_at timestamptz not null default now(),
        updated_at timestamptz not null default now(),
        constraint uq_student_attendances_student_date unique (student_id, service_date),
        check (status in ('expected', 'present', 'left', 'absent', 'leave')),
        check (check_in_source in ('manual', 'nfc')),
        check (check_out_source in ('manual', 'pickup', 'nfc')),
        check (note is null or length(note) <= 200),
        constraint ck_student_attendances_check_in_pair check (
            (check_in_at is null) = (check_in_source is null)
        ),
        constraint ck_student_attendances_check_out_pair check (
            (check_out_at is null) = (check_out_source is null)
        ),
        constraint ck_student_attendances_check_out_after_in check (
            check_out_at is null or (check_in_at is not null and check_out_at >= check_in_at)
        ),
        constraint ck_student_attendances_status_times check (
            (status not in ('present', 'left') or check_in_at is not null)
            and (status <> 'left' or check_out_at is not null)
        ),
        -- 請假狀態一定來自某筆請假；請假取消回 expected 時 leave_id 一併清空
        constraint ck_student_attendances_leave_link check (
            (status = 'leave') = (leave_id is not null)
        )
    )
    """,
    # 每日看板 / 儀表板
    """
    create index ix_student_attendances_date_status
    on public.student_attendances (service_date, status)
    """,
    # 取消請假時回復出勤
    """
    create index ix_student_attendances_leave on public.student_attendances (leave_id)
    where leave_id is not null
    """,
    """
    create index ix_student_attendances_updated_by on public.student_attendances (updated_by)
    where updated_by is not null
    """,
    """
    create trigger trg_student_attendances_updated_at before update on public.student_attendances
    for each row execute function public.set_updated_at()
    """,
    "call app_private.grant_backend('public.student_attendances')",
)


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise NotImplementedError("forward-only：以新的 revision 修正，不回滾")
