-- DB-010：schools（合作 / 學生就讀國小清單，domain_spec M2；後台「參考資料」頁可增刪）。
--
-- 被 students.school_id（restrict）引用。不 seed 預設資料（各安親班自行建立）。

create table public.schools (
    id uuid primary key default gen_random_uuid(),
    name text not null,
    short_name text null,
    is_active boolean not null default true,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    check (length(btrim(name)) between 1 and 50),
    check (short_name is null or length(btrim(short_name)) between 1 and 20)
);

-- 名稱不分大小寫、去頭尾空白唯一
create unique index uq_schools_name on public.schools (lower(btrim(name)));

create trigger trg_schools_updated_at before update on public.schools
for each row execute function public.set_updated_at();

call app_private.secure_table('public.schools');
