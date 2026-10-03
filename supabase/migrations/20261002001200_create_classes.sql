-- DB-012：classes（安親班內部班級，domain_spec M3）。
--
-- 參考 ivy Classroom：保留「同學年度班名不分大小寫唯一」；年級改成可混齡的 grade_levels int[]；
-- 軟刪除用 archived_at（封存的舊班不擋新班同名）；老師指派由 DB-013 class_staff 負責。

create table public.classes (
    id uuid primary key default gen_random_uuid(),
    name text not null,
    grade_levels integer[] not null,
    academic_year integer not null,
    sort_order integer not null default 0,
    archived_at timestamptz null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    check (length(btrim(name)) between 1 and 30),
    constraint ck_classes_grade_levels check (
        cardinality(grade_levels) >= 1
        and grade_levels <@ array[1, 2, 3, 4, 5, 6]
        and array_position(grade_levels, null) is null
    ),
    -- 民國學年度，例如 115
    check (academic_year between 100 and 200)
);

create unique index uq_classes_year_name on public.classes (academic_year, lower(btrim(name)))
where archived_at is null;
create index ix_classes_year_sort on public.classes (academic_year, sort_order)
where archived_at is null;
-- 依年級找班（exams 名單用）
create index ix_classes_grade_levels on public.classes using gin (grade_levels);

create trigger trg_classes_updated_at before update on public.classes
for each row execute function public.set_updated_at();

call app_private.secure_table('public.classes');
