-- DB-008：subjects（科目，domain_spec M2；後台「參考資料」頁可增刪）。
--
-- 被 homework_items（set null）、exam_subjects / exam_scores（restrict）引用；有引用時後台「刪除」由
-- 後端改成停用（is_active = false）。預設科目由 DB-036 的 data migration seed。

create table public.subjects (
    id uuid primary key default gen_random_uuid(),
    name text not null,
    sort_order integer not null default 0,
    is_active boolean not null default true,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    check (length(btrim(name)) between 1 and 20)
);

-- 名稱不分大小寫、去頭尾空白唯一（DB-036 的 seed 以此運算式作為 on conflict 目標）
create unique index uq_subjects_name on public.subjects (lower(btrim(name)));
create index ix_subjects_sort on public.subjects (is_active, sort_order);

create trigger trg_subjects_updated_at before update on public.subjects
for each row execute function public.set_updated_at();

call app_private.secure_table('public.subjects');
