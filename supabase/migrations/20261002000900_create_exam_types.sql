-- DB-009：exam_types（考試類型，domain_spec M2；後台「參考資料」頁可增刪）。
--
-- 被 exams.exam_type_id（restrict）引用。預設考試類型由 DB-037 的 data migration seed。

create table public.exam_types (
    id uuid primary key default gen_random_uuid(),
    name text not null,
    sort_order integer not null default 0,
    is_active boolean not null default true,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    check (length(btrim(name)) between 1 and 20)
);

-- 名稱不分大小寫、去頭尾空白唯一（DB-037 的 seed 以此運算式作為 on conflict 目標）
create unique index uq_exam_types_name on public.exam_types (lower(btrim(name)));
create index ix_exam_types_sort on public.exam_types (is_active, sort_order);

create trigger trg_exam_types_updated_at before update on public.exam_types
for each row execute function public.set_updated_at();

call app_private.secure_table('public.exam_types');
