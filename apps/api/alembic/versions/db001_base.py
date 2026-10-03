"""base：全域基礎設施（DB-041；architecture_decisions §5），不建任何業務表。

1. extensions schema 與 pgcrypto（gen_random_uuid() 是 PG13+ 內建，pgcrypto 留給 digest() 等雜湊）
2. 私有 schema app_private：放 grant_backend 之類不給後端角色呼叫的工具
3. 共用 trigger function public.set_updated_at()
4. 後端專用角色 app_backend：nologin、noinherit、nobypassrls；login 與密碼不寫在 revision
   （雲端由 `python -m app.cli migrate` 依 DATABASE_URL 設定，本機由 `just db-reset` 設定）
5. procedure app_private.grant_backend(regclass, text)：每張表的 revision 最後都呼叫它
6. owner 新建 function 的預設權限：不對 PUBLIC 開放 EXECUTE

權限模型：資料只經 FastAPI 存取，存取控制在應用層；DB 層以最小權限角色把關，不使用 RLS。
每張表的 ACL 只有 owner 與 app_backend。alembic_version 由 Alembic 建立、屬 owner，不授權給
app_backend。全部語句冪等（if not exists / create or replace）。

Revision ID: db001
Revises:
Create Date: 2026-10-03 22:40:03.859007
"""

from collections.abc import Sequence

from alembic import op

revision: str = "db001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EXTENSIONS_SQL = (
    "create schema if not exists extensions",
    "create extension if not exists pgcrypto with schema extensions",
    "revoke create on schema extensions from public",
)

APP_PRIVATE_SQL = (
    "create schema if not exists app_private",
    "revoke all on schema app_private from public",
)

# trigger 觸發不檢查 EXECUTE，收回 PUBLIC 後各表照常掛
SET_UPDATED_AT_SQL = (
    """
    create or replace function public.set_updated_at()
    returns trigger
    language plpgsql
    as $$
    begin
        new.updated_at := now();
        return new;
    end
    $$
    """,
    "revoke all on function public.set_updated_at() from public",
)

# 角色是 cluster 層級：同一個 Postgres 上已存在就略過（本機 db-reset 只重建 database，不刪角色）
APP_BACKEND_SQL = (
    """
    do $$
    begin
        if not exists (select 1 from pg_roles where rolname = 'app_backend') then
            create role app_backend nologin noinherit nobypassrls nocreatedb nocreaterole;
        end if;
    end
    $$
    """,
    "grant usage on schema public to app_backend",
    "revoke create on schema public from public",
)

# 用法：`call app_private.grant_backend('public.students');`
# append-only 的表：`call app_private.grant_backend('public.audit_logs', 'select, insert');`
# p_privileges 只接受由 select / insert / update / delete 組成的逗號清單（去空白、去重、不分
# 大小寫），其他一律 raise，防止注入。
GRANT_BACKEND_SQL = (
    """
    create or replace procedure app_private.grant_backend(
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
            raise exception
                'grant_backend: p_privileges 不可為空（%）', coalesce(p_privileges, 'null');
        end if;

        v_items := array(
            select distinct lower(btrim(x))
            from unnest(string_to_array(p_privileges, ',')) as x
        );
        foreach v_item in array v_items loop
            if v_item <> all (v_allowed) then
                raise exception
                    'grant_backend: p_privileges 只接受 select / insert / update / delete，'
                    '收到「%」',
                    p_privileges;
            end if;
        end loop;

        execute format('revoke all on table %s from public', p_table);
        execute format(
            'grant %s on table %s to app_backend', array_to_string(v_items, ', '), p_table
        );
    end
    $$
    """,
    "revoke all on procedure app_private.grant_backend(regclass, text) from public",
)

# function 對 PUBLIC 的 EXECUTE 是內建預設，per-schema 的 default privileges 無法收回，所以不帶
# in schema；不帶 for role，作用於執行 migration 的 owner。之後被 CHECK、DEFAULT 或後端 SQL 直接
# 呼叫的 function 由該表的 revision 明確 grant execute 給 app_backend。
DEFAULT_PRIVILEGES_SQL = ("alter default privileges revoke execute on functions from public",)


def upgrade() -> None:
    for statements in (
        EXTENSIONS_SQL,
        APP_PRIVATE_SQL,
        SET_UPDATED_AT_SQL,
        APP_BACKEND_SQL,
        GRANT_BACKEND_SQL,
        DEFAULT_PRIVILEGES_SQL,
    ):
        for statement in statements:
            op.execute(statement)


def downgrade() -> None:
    raise NotImplementedError("forward-only：以新的 revision 修正，不回滾")
