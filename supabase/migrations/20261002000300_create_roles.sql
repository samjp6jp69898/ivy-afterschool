-- DB-003：roles（角色與權限碼，domain_spec M1）。
--
-- 權限碼以 text[] 存放，`*` 為萬用碼（只有 admin 可以持有）。權限碼本身的合法性由後端 Permission enum
-- 驗證，DB 不寫死清單（新增權限碼不需 migration）。admin 權限不可改、系統角色不可刪由 BACKEND 的
-- RoleService 把關。預設角色由 DB-035 的 data migration seed。

create table public.roles (
    id uuid primary key default gen_random_uuid(),
    code text not null,
    name text not null,
    description text null,
    permissions text[] not null default '{}',
    is_system boolean not null default false,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint uq_roles_code unique (code),
    check (code ~ '^[a-z][a-z0-9_]{1,31}$'),
    check (length(btrim(name)) between 1 and 50),
    constraint ck_roles_wildcard_admin_only check (code = 'admin' or not ('*' = any (permissions))),
    constraint ck_roles_permissions_no_null check (array_position(permissions, null) is null)
);

create trigger trg_roles_updated_at before update on public.roles
for each row execute function public.set_updated_at();

call app_private.secure_table('public.roles');
