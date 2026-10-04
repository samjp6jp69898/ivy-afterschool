"""enforce_grants_baseline：public 物件權限收尾與 fail-closed 自我驗證（DB-040；§5）。

第二道防線：各表 revision 已各自呼叫 app_private.grant_backend（第一道防線），本檔從 pg_catalog
取執行當下實際存在的物件補漏，不寫死清單（寫死的清單會和漏掉的表一起漏掉）。

1. 迴圈 public 的 table / sequence 與 public、app_private 的 routine：revoke all ... from public。
   不在迴圈內 grant app_backend：授權邊界由各表 revision 明確決定（例如 audit_logs 只有
   select / insert），已明確 grant 給 app_backend 的權限不受 revoke PUBLIC 影響。
2. 補建 FK 欄位的非 partial 索引：students.class_id、guardians.student_id、
   pickup_persons.student_id、parent_binding_codes.guardian_id 原本只有 predicate 不是
   `<欄位> is not null` 的 partial 前導索引（archived_at is null / used_at is null），
   父表刪除 / 更新時的 FK 檢查用不到，會退回全表掃描。
3. VERIFY_SQL（fail-closed）：任一條成立就 raise exception 讓 migration 失敗，訊息列出違規表名：
   - public 有表開啟 RLS（本專案不使用 RLS；開啟而沒有 policy 會讓 app_backend 讀不到資料）；
   - 任何 public 表的 ACL 出現 owner 與 app_backend 以外的 grantee（含 PUBLIC）；
   - 除 alembic_version 外，有 public 表 app_backend 沒有任何權限；
   - app_backend 對 alembic_version 有任何權限。

本檔只在套用當下執行一次：之後新增的表若 revision 排在本檔之後，本檔不會重跑。持續把關者是
第一道防線（grant_backend）與 tests/integration/db/ 的 test_grants_baseline.py、
test_schema_conventions.py（integration CI job 每次都跑）。全部語句冪等。

Revision ID: db040
Revises: db028
Create Date: 2026-10-04 21:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "db040"
down_revision: str | Sequence[str] | None = "db028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


REVOKE_PUBLIC_SQL = (
    """
    do $$
    declare
        v_rel regclass;
    begin
        for v_rel in
            select c.oid
            from pg_class c
            join pg_namespace n on n.oid = c.relnamespace
            where n.nspname = 'public' and c.relkind in ('r', 'p')
        loop
            execute format('revoke all on table %s from public', v_rel);
        end loop;
    end
    $$
    """,
    """
    do $$
    declare
        v_rel regclass;
    begin
        for v_rel in
            select c.oid
            from pg_class c
            join pg_namespace n on n.oid = c.relnamespace
            where n.nspname = 'public' and c.relkind = 'S'
        loop
            execute format('revoke all on sequence %s from public', v_rel);
        end loop;
    end
    $$
    """,
    # routine 同時涵蓋 function 與 procedure
    """
    do $$
    declare
        v_routine regprocedure;
    begin
        for v_routine in
            select p.oid
            from pg_proc p
            join pg_namespace n on n.oid = p.pronamespace
            where n.nspname in ('public', 'app_private')
        loop
            execute format('revoke all on routine %s from public', v_routine);
        end loop;
    end
    $$
    """,
)

FK_INDEX_SQL = (
    "create index if not exists ix_students_class_id on public.students (class_id)",
    "create index if not exists ix_guardians_student_id on public.guardians (student_id)",
    """
    create index if not exists ix_pickup_persons_student_id
    on public.pickup_persons (student_id)
    """,
    """
    create index if not exists ix_parent_binding_codes_guardian_id
    on public.parent_binding_codes (guardian_id)
    """,
)

# aclexplode 的 grantee 為 0 代表 PUBLIC；relacl 為 null（從未 grant / revoke 過）時展開為零列，
# 這種表會被「app_backend 沒有任何權限」那條抓到。
VERIFY_SQL = """
do $$
declare
    v_backend_oid oid := (select oid from pg_roles where rolname = 'app_backend');
    v_rls text;
    v_foreign_grantee text;
    v_ungranted text;
    v_alembic_privileges text;
    v_problems text[] := array[]::text[];
begin
    select string_agg(c.relname, ', ' order by c.relname) into v_rls
    from pg_class c
    join pg_namespace n on n.oid = c.relnamespace
    where n.nspname = 'public' and c.relkind in ('r', 'p')
      and (c.relrowsecurity or c.relforcerowsecurity);
    if v_rls is not null then
        v_problems := v_problems || format('開啟 RLS 的表：%s', v_rls);
    end if;

    select string_agg(distinct c.relname, ', ') into v_foreign_grantee
    from pg_class c
    join pg_namespace n on n.oid = c.relnamespace
    cross join lateral aclexplode(c.relacl) a
    where n.nspname = 'public' and c.relkind in ('r', 'p')
      and a.grantee <> c.relowner
      and a.grantee is distinct from v_backend_oid;
    if v_foreign_grantee is not null then
        v_problems := v_problems
            || format('ACL 出現 owner 與 app_backend 以外的 grantee 的表：%s', v_foreign_grantee);
    end if;

    select string_agg(c.relname, ', ' order by c.relname) into v_ungranted
    from pg_class c
    join pg_namespace n on n.oid = c.relnamespace
    where n.nspname = 'public' and c.relkind in ('r', 'p')
      and c.relname <> 'alembic_version'
      and not exists (
          select 1 from aclexplode(c.relacl) a where a.grantee = v_backend_oid
      );
    if v_ungranted is not null then
        v_problems := v_problems || format('app_backend 沒有任何權限的表：%s', v_ungranted);
    end if;

    select string_agg(a.privilege_type, ', ' order by a.privilege_type) into v_alembic_privileges
    from pg_class c
    join pg_namespace n on n.oid = c.relnamespace
    cross join lateral aclexplode(c.relacl) a
    where n.nspname = 'public' and c.relname = 'alembic_version' and a.grantee = v_backend_oid;
    if v_alembic_privileges is not null then
        v_problems := v_problems
            || format('app_backend 對 alembic_version 有權限：%s', v_alembic_privileges);
    end if;

    if cardinality(v_problems) > 0 then
        raise exception 'db040 grant 收尾驗證失敗：%', array_to_string(v_problems, '；');
    end if;
end
$$
"""


def upgrade() -> None:
    for statement in (*REVOKE_PUBLIC_SQL, *FK_INDEX_SQL, VERIFY_SQL):
        op.execute(statement)


def downgrade() -> None:
    raise NotImplementedError("forward-only：以新的 revision 修正，不回滾")
