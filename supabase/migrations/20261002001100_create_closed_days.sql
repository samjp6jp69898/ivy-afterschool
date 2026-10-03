-- DB-011：closed_days（安親班休息日，domain_spec M2）。出勤每日初始化（BACKEND）跳過這些日期。
--
-- uq_closed_days_date 本身即提供依日期查詢的索引，不另建。

create table public.closed_days (
    id uuid primary key default gen_random_uuid(),
    date date not null,
    reason text null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint uq_closed_days_date unique (date),
    check (reason is null or length(reason) <= 100)
);

create trigger trg_closed_days_updated_at before update on public.closed_days
for each row execute function public.set_updated_at();

call app_private.secure_table('public.closed_days');
