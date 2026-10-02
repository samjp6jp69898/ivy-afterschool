#!/usr/bin/env python3
"""INFRA-007：找出 schema 內未開 RLS、或對 anon / authenticated / service_role 開放的 relation。

依 docs/architecture_decisions.md §5：RLS 一律開啟，且不給 anon / authenticated 任何 policy 或
privilege；service_role 對 public 的權限也已收回（DB-001），一併受檢。用 apps/api/.venv 的 python
執行（需要 psycopg），只跑 select，完全唯讀。

受檢集合一律從 pg_catalog 動態列舉，不寫死表名：新增一張表就自動納入檢查。schema 內沒有任何表時
視為結果無意義（空集合上的「全部合格」是恆真），沒帶 --allow-empty 就 exit 2。

exit code：0 沒有違規；1 有違規（逐行印 `relation  kind  detail`）；2 參數 / 連線 / 空 schema 等
無法給出結論的情況。
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg.conninfo import conninfo_to_dict

DEFAULT_DB_URL = "postgresql://postgres:postgres@127.0.0.1:54342/postgres"
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

#: 不得持有任何 policy 的角色（public 代表 policy 對所有角色生效）。
FORBIDDEN_POLICY_ROLES = ("anon", "authenticated", "service_role", "public")
#: 不得持有任何 privilege 的角色；aclexplode 的 grantee 0 是 PUBLIC，等同授給 anon，一併視為違規。
FORBIDDEN_GRANTEES = ("anon", "authenticated", "service_role", "public")

_TABLES_SQL = """
select n.nspname || '.' || c.relname, c.relrowsecurity
from pg_class c
join pg_namespace n on n.oid = c.relnamespace
where n.nspname = %(schema)s and c.relkind in ('r', 'p')
"""

_POLICIES_SQL = """
select n.nspname || '.' || c.relname, p.polname,
       case when p.polroles = '{0}'::oid[] then array['public']
            else array(select r.rolname::text from pg_roles r where r.oid = any(p.polroles))
       end
from pg_policy p
join pg_class c on c.oid = p.polrelid
join pg_namespace n on n.oid = c.relnamespace
where n.nspname = %(schema)s
"""

_GRANTS_SQL = """
select n.nspname || '.' || c.relname,
       case when a.grantee = 0 then 'public' else a.grantee::regrole::text end,
       a.privilege_type
from pg_class c
join pg_namespace n on n.oid = c.relnamespace
cross join lateral aclexplode(c.relacl) as a
where n.nspname = %(schema)s and c.relkind in ('r', 'p', 'v', 'm', 'S')
"""


@dataclass(frozen=True, order=True)
class Violation:
    relation: str  # schema.name
    kind: str  # 'rls_disabled' | 'policy' | 'grant'
    detail: str  # 例如 'policy p_read to anon'、'SELECT to authenticated'


def list_tables(conn: psycopg.Connection[Any], schema: str) -> list[tuple[str, bool]]:
    """schema 內的表（含分割表）與其 RLS 是否開啟。"""
    return [(str(rel), bool(rls)) for rel, rls in conn.execute(_TABLES_SQL, {"schema": schema})]


def find_violations(conn: psycopg.Connection[Any], schema: str = "public") -> list[Violation]:
    violations: list[Violation] = []

    for relation, rls_enabled in list_tables(conn, schema):
        if not rls_enabled:
            violations.append(Violation(relation, "rls_disabled", "row level security 未開啟"))

    for relation, policy, roles in conn.execute(_POLICIES_SQL, {"schema": schema}):
        forbidden = [role for role in roles if role in FORBIDDEN_POLICY_ROLES]
        if forbidden:
            detail = f"policy {policy} to {', '.join(forbidden)}"
            violations.append(Violation(str(relation), "policy", detail))

    for relation, grantee, privilege in conn.execute(_GRANTS_SQL, {"schema": schema}):
        if grantee in FORBIDDEN_GRANTEES:
            violations.append(Violation(str(relation), "grant", f"{privilege} to {grantee}"))

    return sorted(violations)


def is_loopback_url(url: str) -> bool:
    """host（多主機逐項）與 hostaddr 都是本機 loopback 才回 True。"""
    try:
        params = conninfo_to_dict(url)
    except psycopg.ProgrammingError:
        return False
    host = str(params.get("host", ""))
    if not host:
        return False
    if any(part not in LOOPBACK_HOSTS for part in host.split(",")):
        return False
    hostaddr = str(params.get("hostaddr", ""))
    return all(part in ("", "127.0.0.1", "::1") for part in hostaddr.split(","))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="檢查 schema 內每張表都開了 RLS，且 anon / authenticated / service_role "
        "沒有任何 policy 或 privilege（唯讀）。"
    )
    parser.add_argument("--db-url", default=DEFAULT_DB_URL, help=f"預設 {DEFAULT_DB_URL}")
    parser.add_argument("--schema", default="public", help="受檢 schema（預設 public）")
    parser.add_argument(
        "--allow-empty", action="store_true", help="schema 內沒有任何表時視為通過（exit 0）"
    )
    parser.add_argument(
        "--allow-remote", action="store_true", help="允許連非本機的 DB（對雲端驗證時明確開啟）"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if not args.allow_remote and not is_loopback_url(args.db_url):
        print(
            "錯誤：--db-url 不是本機 loopback；對雲端 DB 檢查請明確帶 --allow-remote。",
            file=sys.stderr,
        )
        return 2

    try:
        conn = psycopg.connect(args.db_url, connect_timeout=5)
    except psycopg.OperationalError as exc:
        print(
            f"錯誤：無法連線 DB（{exc.__class__.__name__}），本機請先 just db-start",
            file=sys.stderr,
        )
        return 2

    with conn:
        conn.read_only = True
        tables = list_tables(conn, args.schema)
        if not tables and not args.allow_empty:
            print(
                f"錯誤：schema {args.schema} 沒有任何表，檢查結果無意義"
                "（確定要略過請帶 --allow-empty）",
                file=sys.stderr,
            )
            return 2
        violations = find_violations(conn, schema=args.schema)

    if violations:
        for violation in violations:
            print(f"{violation.relation}  {violation.kind}  {violation.detail}")
        print(
            f"共 {len(violations)} 項違規（schema {args.schema}，{len(tables)} 張表）",
            file=sys.stderr,
        )
        return 1

    print(f"schema {args.schema}：{len(tables)} 張表全部開啟 RLS，且無對外 policy / grant")
    return 0


if __name__ == "__main__":
    sys.exit(main())
