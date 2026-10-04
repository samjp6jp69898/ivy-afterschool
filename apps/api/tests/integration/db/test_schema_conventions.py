"""DB-040：schema 慣例（architecture_decisions §5「資料慣例」），不寫死表名。

表清單從 information_schema 動態取全部 public base table（排除 alembic_version）。每條慣例都有一個
「刻意不符的臨時表」反例，證明檢查函式真的會回報該表而不是恆真；臨時表只在 owner transaction 內
可見，結束 rollback，所以檢查一律以 `owner_conn` 執行。函式名稱以 `test_grants_baseline_` 開頭供
DB-040 的 tdd.run 以 -k 選取。

FK 索引規則：FK 欄位要有以它為前導欄位的 index / unique（多欄 FK 則前導 n 欄恰為 FK 欄位集合）。
partial index 只在 predicate 恰為 `<fk 欄位> is not null` 時算數（父表刪除 / 更新時的 FK 檢查是
`fk = $1`，null 列本來就不可能命中，planner 會用它）；其他 predicate（例如 `archived_at is null`）
排除掉的列仍可能命中，FK 檢查會退回全表掃描，不算。
"""

import pytest

from tests.integration.db.conftest import Conn

_BASE_TABLES_SQL = """
    select table_name
    from information_schema.tables
    where table_schema = 'public' and table_type = 'BASE TABLE'
      and table_name <> 'alembic_version'
    order by table_name
"""
_COLUMNS_SQL = """
    select column_name, data_type, is_nullable, column_default
    from information_schema.columns
    where table_schema = 'public' and table_name = %s
      and column_name in ('id', 'created_at', 'updated_at')
"""
_PRIMARY_KEY_SQL = """
    select array_agg(a.attname order by k.ord)
    from pg_constraint c
    cross join lateral unnest(c.conkey) with ordinality as k(attnum, ord)
    join pg_attribute a on a.attrelid = c.conrelid and a.attnum = k.attnum
    where c.contype = 'p' and c.conrelid = to_regclass(format('%%I.%%I', 'public', %s::text))
"""
# tgtype bit：1 = row、2 = before、16 = update
_UPDATED_AT_TRIGGER_SQL = """
    select count(*)
    from pg_trigger
    where tgrelid = to_regclass(format('%%I.%%I', 'public', %s::text))
      and not tgisinternal
      and tgfoid = 'public.set_updated_at'::regproc
      and tgtype & 19 = 19
"""
_NAIVE_TIMESTAMP_SQL = """
    select table_name, column_name
    from information_schema.columns
    where table_schema = 'public' and data_type = 'timestamp without time zone'
    order by 1, 2
"""
_FK_WITHOUT_INDEX_SQL = """
    select cl.relname, c.conname,
           (select array_agg(a.attname order by k.ord)
              from unnest(c.conkey) with ordinality as k(attnum, ord)
              join pg_attribute a on a.attrelid = c.conrelid and a.attnum = k.attnum)
    from pg_constraint c
    join pg_class cl on cl.oid = c.conrelid
    join pg_namespace n on n.oid = cl.relnamespace
    where n.nspname = 'public' and c.contype = 'f'
      and not exists (
        select 1
        from pg_index i
        where i.indrelid = c.conrelid
          and (select array_agg(k.attnum order by k.attnum)
                 from unnest(i.indkey::int2[]) with ordinality as k(attnum, ord)
                where k.ord <= array_length(c.conkey, 1))
              = (select array_agg(x order by x) from unnest(c.conkey) x)
          and (
            i.indpred is null
            or (
              array_length(c.conkey, 1) = 1
              and pg_get_expr(i.indpred, i.indrelid) = format(
                '(%I IS NOT NULL)',
                (select a.attname from pg_attribute a
                  where a.attrelid = c.conrelid and a.attnum = c.conkey[1])
              )
            )
          )
      )
    order by 1, 2
"""
_TIMESTAMPTZ_NOT_NULL_NOW = ("timestamp with time zone", "NO", "now()")


def _public_base_tables(conn: Conn) -> list[str]:
    return [name for (name,) in conn.execute(_BASE_TABLES_SQL).fetchall()]


def _table_violations(conn: Conn, table: str) -> list[str]:
    columns = {
        name: (data_type, nullable, default)
        for name, data_type, nullable, default in conn.execute(_COLUMNS_SQL, (table,)).fetchall()
    }
    problems: list[str] = []
    if columns.get("id", (None,))[0] != "uuid" or columns["id"][2] != "gen_random_uuid()":
        problems.append(f"id 應為 uuid default gen_random_uuid()：{columns.get('id')}")
    (primary_key,) = conn.execute(_PRIMARY_KEY_SQL, (table,)).fetchone() or (None,)
    if primary_key != ["id"]:
        problems.append(f"主鍵應為 (id)：{primary_key}")
    for column in ("created_at", "updated_at"):
        if columns.get(column) != _TIMESTAMPTZ_NOT_NULL_NOW:
            problems.append(
                f"{column} 應為 timestamptz not null default now()：{columns.get(column)}"
            )
    (trigger_count,) = conn.execute(_UPDATED_AT_TRIGGER_SQL, (table,)).fetchone() or (0,)
    if trigger_count != 1:
        problems.append(
            f"應有恰一個 before update 執行 public.set_updated_at() 的 trigger：{trigger_count}"
        )
    return problems


def _convention_violations(conn: Conn, tables: list[str]) -> dict[str, list[str]]:
    """每張不符慣例的表 → 違反項目描述；全部符合時為空 dict。"""
    return {table: problems for table in tables if (problems := _table_violations(conn, table))}


def _naive_timestamp_columns(conn: Conn) -> list[tuple[str, str]]:
    return [(table, column) for table, column in conn.execute(_NAIVE_TIMESTAMP_SQL).fetchall()]


def _fk_columns_without_index(conn: Conn) -> list[tuple[str, str, list[str]]]:
    return [
        (table, constraint, columns)
        for table, constraint, columns in conn.execute(_FK_WITHOUT_INDEX_SQL).fetchall()
    ]


def test_grants_baseline_schema_conventions(owner_conn: Conn) -> None:
    tables = _public_base_tables(owner_conn)
    assert {"students", "audit_logs", "roles"} <= set(tables)
    assert _convention_violations(owner_conn, tables) == {}

    owner_conn.execute(
        """
        create table public._no_updated_at(
            id uuid primary key default gen_random_uuid(),
            created_at timestamptz not null default now()
        )
        """
    )
    violations = _convention_violations(owner_conn, _public_base_tables(owner_conn))
    assert set(violations) == {"_no_updated_at"}
    assert [problem for problem in violations["_no_updated_at"] if "updated_at" in problem] == [
        "updated_at 應為 timestamptz not null default now()：None",
        "應有恰一個 before update 執行 public.set_updated_at() 的 trigger：0",
    ]


_FULL_TABLE = """
    create table public._probe(
        id uuid primary key default gen_random_uuid(),
        created_at timestamptz not null default now(),
        updated_at timestamptz not null default now()
    )
"""
_TRIGGER = """
    create trigger trg_probe_updated_at before update on public._probe
    for each row execute function public.set_updated_at()
"""


@pytest.mark.parametrize(
    ("statements", "expected_fragment"),
    [
        pytest.param(
            (
                _FULL_TABLE.replace(
                    "id uuid primary key default gen_random_uuid()", "id bigint primary key"
                ),
                _TRIGGER,
            ),
            "id 應為 uuid default gen_random_uuid()",
            id="id_not_uuid",
        ),
        pytest.param(
            (_FULL_TABLE.replace("default gen_random_uuid()", ""), _TRIGGER),
            "id 應為 uuid default gen_random_uuid()",
            id="id_without_default",
        ),
        pytest.param(
            (
                _FULL_TABLE.replace(
                    "created_at timestamptz not null", "created_at timestamptz null"
                ),
                _TRIGGER,
            ),
            "created_at 應為 timestamptz not null default now()",
            id="created_at_nullable",
        ),
        pytest.param(
            (
                _FULL_TABLE.replace(
                    "updated_at timestamptz not null default now()",
                    "updated_at timestamptz not null",
                ),
                _TRIGGER,
            ),
            "updated_at 應為 timestamptz not null default now()",
            id="updated_at_without_default",
        ),
        pytest.param(
            (_FULL_TABLE,),
            "應有恰一個 before update 執行 public.set_updated_at() 的 trigger：0",
            id="missing_trigger",
        ),
        pytest.param(
            (_FULL_TABLE, _TRIGGER.replace("before update", "after update")),
            "應有恰一個 before update 執行 public.set_updated_at() 的 trigger：0",
            id="after_update_trigger",
        ),
    ],
)
def test_grants_baseline_schema_conventions_reports_each_rule(
    owner_conn: Conn, statements: tuple[str, ...], expected_fragment: str
) -> None:
    for statement in statements:
        owner_conn.execute(statement)

    violations = _convention_violations(owner_conn, _public_base_tables(owner_conn))
    assert set(violations) == {"_probe"}
    assert [problem for problem in violations["_probe"] if problem.startswith(expected_fragment)]


def test_grants_baseline_schema_conventions_accepts_compliant_table(owner_conn: Conn) -> None:
    owner_conn.execute(_FULL_TABLE)
    owner_conn.execute(_TRIGGER)

    assert _convention_violations(owner_conn, _public_base_tables(owner_conn)) == {}


def test_grants_baseline_no_naive_timestamps(owner_conn: Conn) -> None:
    assert _naive_timestamp_columns(owner_conn) == []

    owner_conn.execute("create table public._naive(id int, at timestamp)")
    assert _naive_timestamp_columns(owner_conn) == [("_naive", "at")]


def test_grants_baseline_fk_columns_indexed(owner_conn: Conn) -> None:
    assert _fk_columns_without_index(owner_conn) == []

    owner_conn.execute(
        """
        create table public._child(
            id uuid primary key default gen_random_uuid(),
            student_id uuid null references public.students (id),
            archived_at timestamptz null
        )
        """
    )
    missing = [("_child", "_child_student_id_fkey", ["student_id"])]
    assert _fk_columns_without_index(owner_conn) == missing

    # predicate 不是 <fk 欄位> is not null 的 partial index 不算
    owner_conn.execute(
        "create index _child_archived on public._child (student_id) where archived_at is null"
    )
    assert _fk_columns_without_index(owner_conn) == missing

    # 後導欄位不算
    owner_conn.execute("create index _child_trailing on public._child (archived_at, student_id)")
    assert _fk_columns_without_index(owner_conn) == missing

    # 含 <fk 欄位> is not null 的複合 predicate 仍排除了可能命中的列，不算
    # （predicate 比對是完全相等，不是字串包含）
    owner_conn.execute(
        """
        create index _child_compound on public._child (student_id)
        where student_id is not null and archived_at is null
        """
    )
    assert _fk_columns_without_index(owner_conn) == missing

    # predicate 恰為 <fk 欄位> is not null 的 partial index 算
    owner_conn.execute(
        "create index _child_not_null on public._child (student_id) where student_id is not null"
    )
    assert _fk_columns_without_index(owner_conn) == []

    owner_conn.execute("drop index public._child_not_null")
    assert _fk_columns_without_index(owner_conn) == missing

    # 非 partial 的前導索引算（多欄也算）
    owner_conn.execute("create index _child_leading on public._child (student_id, archived_at)")
    assert _fk_columns_without_index(owner_conn) == []
