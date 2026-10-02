-- DB-001：本專案第一支 migration，只放全域基礎設施，不建任何業務表。
--
-- 內容：
--   (1) extensions：pgcrypto（gen_random_uuid() 是 PG13+ 內建，pgcrypto 留給 digest() 等雜湊工具）
--   (2) 私有 schema app_private：放 secure_table 之類不該被 PostgREST 看到的工具
--   (3) 共用 trigger function public.set_updated_at()
--   (4) 後端專用角色 app_backend：nologin、noinherit、nobypassrls；密碼與 login 不寫在 migration
--   (5) procedure app_private.secure_table(regclass, text)：每張表 migration 最後都呼叫它
--   (6) postgres 在 public 的預設授權收回 anon / authenticated / service_role
--
-- 連線角色設計：後端一律以 app_backend 連線，且刻意不帶 BYPASSRLS。每張表靠 secure_table 建立的
-- app_backend_all policy 放行；忘了呼叫 secure_table 的表 app_backend 連讀都讀不到，會在整合測試
-- 立即暴露。anon / authenticated / service_role 對 public 的權限全部收回，Supabase 的 PostgREST /
-- Realtime 對外不暴露任何業務資料；service_role key 即使外洩，爆炸半徑只剩 storage（DB-034）。
--
-- 部署備忘（雲端第一次 `supabase db push` 之後，在 SQL editor 一次性執行）：
--   alter role app_backend login password '<隨機 32 字元以上>';
-- Railway 的 DATABASE_URL 以該角色經 Supavisor（session mode，user 格式 app_backend.<project_ref>）
-- 或 direct connection 連線；之後改密碼同一指令，不需 migration。
-- 本機：DB-039 的 supabase/seed.sql 設定 `app_backend_local`（只在 just db-reset 跑，雲端不執行 seed）。
--
-- 關於預設授權：Supabase 對 supabase_admin 建立的物件另有一組預設授權，本專案所有物件都以 postgres
-- 建立（CLI 的 migration 與 seed 都以 postgres 連線），因此只需改 postgres 的預設值。
-- 本檔不碰 storage schema（DB-034 處理）。

-- ---------------------------------------------------------------------------
-- (1) extensions
-- ---------------------------------------------------------------------------
create extension if not exists pgcrypto with schema extensions;

-- ---------------------------------------------------------------------------
-- (2) 私有 schema：不在 config.toml 的 api.schemas，PostgREST 看不到；只有 owner（postgres）能用
-- ---------------------------------------------------------------------------
create schema if not exists app_private;
revoke all on schema app_private from public, anon, authenticated, service_role;

-- ---------------------------------------------------------------------------
-- (3) 共用 trigger function：各表以 `before update ... for each row execute function public.set_updated_at()` 掛上
-- ---------------------------------------------------------------------------
create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
    new.updated_at := now();
    return new;
end
$$;

revoke all on function public.set_updated_at() from public, anon, authenticated, service_role;

-- ---------------------------------------------------------------------------
-- (4) 後端專用角色 app_backend
-- ---------------------------------------------------------------------------
--
-- nologin：login 與密碼由本機 seed / 雲端部署者另外設定，migration 內不含任何密碼。
-- noinherit：不透過成員身分繼承別的角色權限。
-- nobypassrls：policy 才有意義（見檔頭）。冪等：已存在就略過。
do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'app_backend') then
        create role app_backend nologin noinherit nobypassrls;
    end if;
end
$$;

grant usage on schema public to app_backend;

-- postgres 建立角色時只取得 ADMIN OPTION（PG16+ 的 createrole_self_grant 預設為空，SET / INHERIT
-- 皆為 false），無法 `set role app_backend`。整合測試驗證 migration 時需在同一 owner transaction 內
-- `set local role app_backend`（DB-002 的 as_role），所以明確授與 SET；不開 INHERIT，postgres 不會
-- 因此多出任何權限（它本來就比 app_backend 大）。
grant app_backend to postgres with set true, inherit false;

-- ---------------------------------------------------------------------------
-- (5) app_private.secure_table：統一開 RLS 並只對 app_backend 放行
-- ---------------------------------------------------------------------------
--
-- 用法：每張表的 migration 最後 `call app_private.secure_table('public.students');`
-- append-only 的表（audit_logs）以 `call app_private.secure_table('public.audit_logs', 'select, insert');`
-- p_privileges 只接受由 select / insert / update / delete 組成的逗號清單，其他一律 raise，防止注入。
create or replace procedure app_private.secure_table(
    p_table regclass,
    p_privileges text default 'select, insert, update, delete'
)
language plpgsql
as $$
declare
    v_allowed constant text[] := array['select', 'insert', 'update', 'delete'];
    v_items text[];
    v_item text;
begin
    if p_privileges is null or btrim(p_privileges) = '' then
        raise exception 'secure_table: p_privileges 不可為空（%）', coalesce(p_privileges, 'null');
    end if;

    v_items := array(
        select distinct lower(btrim(x))
        from unnest(string_to_array(p_privileges, ',')) as x
    );
    foreach v_item in array v_items loop
        if v_item <> all (v_allowed) then
            raise exception
                'secure_table: p_privileges 只接受 select / insert / update / delete 的逗號清單，收到「%」',
                p_privileges;
        end if;
    end loop;

    execute format('alter table %s enable row level security', p_table);
    execute format('alter table %s force row level security', p_table);
    execute format('revoke all on table %s from public, anon, authenticated, service_role', p_table);
    execute format('grant %s on table %s to app_backend', array_to_string(v_items, ', '), p_table);
    execute format('drop policy if exists app_backend_all on %s', p_table);
    execute format(
        'create policy app_backend_all on %s for all to app_backend using (true) with check (true)',
        p_table
    );
end
$$;

revoke all on procedure app_private.secure_table(regclass, text) from public, anon, authenticated, service_role;

-- ---------------------------------------------------------------------------
-- (6) postgres 在 public 的預設授權：收回 anon / authenticated / service_role
-- ---------------------------------------------------------------------------
--
-- Supabase 預設以 per-schema default privileges 把 public 的 tables / sequences / functions 全部授給
-- anon、authenticated、service_role；這裡逐一反轉。日後任何以 postgres 建立的 public 物件在建立當下
-- 就不對這三個角色開放，secure_table 的 revoke 只是第二道保險。
alter default privileges for role postgres in schema public
    revoke all on tables from anon, authenticated, service_role;
alter default privileges for role postgres in schema public
    revoke all on sequences from anon, authenticated, service_role;
alter default privileges for role postgres in schema public
    revoke all on functions from anon, authenticated, service_role;

-- function 內建預設會把 EXECUTE 授給 PUBLIC，而 per-schema 的 default privileges 只能疊加在全域 / 內建
-- 預設之上、無法反向收回（PostgreSQL 文件：ALTER DEFAULT PRIVILEGES），所以對 PUBLIC 的收回必須寫成
-- 全域（不帶 in schema）。效果：postgres 之後建立的任何 function / procedure 都要明確 grant execute
-- 才能被其他角色呼叫。
alter default privileges for role postgres
    revoke execute on functions from public;
