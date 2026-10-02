"""INFRA-007：scripts/check_rls.py 的 DB 端 RLS / policy / grant 檢查（integration）。

find_violations 的案例在 local_db_conn 的 transaction 內建立唯一名稱的暫存 schema，結束時 rollback；
main() 走新連線看不到未 commit 的物件，所以 main 的案例先 commit、finally 再 drop schema cascade。
"""

from __future__ import annotations

import importlib.util
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import psycopg
import pytest

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_rls.py"


def _load_check_rls() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_rls_under_test", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def check_rls() -> ModuleType:
    return _load_check_rls()


def _new_schema_name() -> str:
    return f"rls_probe_{uuid.uuid4().hex[:8]}"


def _ensure_app_backend(conn: psycopg.Connection[Any]) -> None:
    exists = conn.execute("select 1 from pg_roles where rolname = 'app_backend'").fetchone()
    if exists is None:
        conn.execute("create role app_backend nologin")


@pytest.fixture
def schema(local_db_conn: psycopg.Connection[Any]) -> str:
    """transaction 內的暫存 schema，由 local_db_conn 結束時 rollback。"""
    name = _new_schema_name()
    local_db_conn.execute(f"create schema {name}")
    return name


@pytest.fixture
def committed_schema(local_db_conn: psycopg.Connection[Any]) -> Iterator[str]:
    """已 commit 的暫存 schema（給 main() 的新連線看），finally drop cascade 並 commit。"""
    name = _new_schema_name()
    local_db_conn.execute(f"create schema {name}")
    local_db_conn.commit()
    try:
        yield name
    finally:
        local_db_conn.rollback()
        local_db_conn.execute(f"drop schema if exists {name} cascade")
        local_db_conn.commit()


def test_check_rls_flags_table_without_rls(
    check_rls: ModuleType, local_db_conn: psycopg.Connection[Any], schema: str
) -> None:
    local_db_conn.execute(f"create table {schema}.t1 (id int)")

    violations = check_rls.find_violations(local_db_conn, schema=schema)

    assert len(violations) == 1
    assert violations[0].relation == f"{schema}.t1"
    assert violations[0].kind == "rls_disabled"


def test_check_rls_clean_table_passes(
    check_rls: ModuleType, local_db_conn: psycopg.Connection[Any], schema: str
) -> None:
    local_db_conn.execute(f"create table {schema}.t2 (id int)")
    local_db_conn.execute(f"alter table {schema}.t2 enable row level security")
    local_db_conn.execute(f"revoke all on table {schema}.t2 from anon, authenticated")

    assert check_rls.find_violations(local_db_conn, schema=schema) == []


def test_check_rls_flags_policy_for_anon(
    check_rls: ModuleType, local_db_conn: psycopg.Connection[Any], schema: str
) -> None:
    local_db_conn.execute(f"create table {schema}.t3 (id int)")
    local_db_conn.execute(f"alter table {schema}.t3 enable row level security")
    local_db_conn.execute(f"create policy p on {schema}.t3 for select to anon using (true)")

    violations = check_rls.find_violations(local_db_conn, schema=schema)

    assert [(v.kind, "anon" in v.detail) for v in violations] == [("policy", True)]


def test_check_rls_flags_policy_for_authenticated(
    check_rls: ModuleType, local_db_conn: psycopg.Connection[Any], schema: str
) -> None:
    local_db_conn.execute(f"create table {schema}.t3 (id int)")
    local_db_conn.execute(f"alter table {schema}.t3 enable row level security")
    local_db_conn.execute(
        f"create policy p on {schema}.t3 for select to authenticated using (true)"
    )

    violations = check_rls.find_violations(local_db_conn, schema=schema)

    assert [(v.kind, "authenticated" in v.detail) for v in violations] == [("policy", True)]


def test_check_rls_ignores_app_backend_policy(
    check_rls: ModuleType, local_db_conn: psycopg.Connection[Any], schema: str
) -> None:
    _ensure_app_backend(local_db_conn)
    local_db_conn.execute(f"create table {schema}.t5 (id int)")
    local_db_conn.execute(f"alter table {schema}.t5 enable row level security")
    local_db_conn.execute(f"revoke all on table {schema}.t5 from anon, authenticated, service_role")
    local_db_conn.execute(
        f"create policy app_backend_all on {schema}.t5 for all to app_backend using (true)"
    )

    assert check_rls.find_violations(local_db_conn, schema=schema) == []


def test_check_rls_flags_grant_to_service_role(
    check_rls: ModuleType, local_db_conn: psycopg.Connection[Any], schema: str
) -> None:
    local_db_conn.execute(f"create table {schema}.t6 (id int)")
    local_db_conn.execute(f"alter table {schema}.t6 enable row level security")
    local_db_conn.execute(f"grant select on table {schema}.t6 to service_role")

    violations = check_rls.find_violations(local_db_conn, schema=schema)

    assert [(v.relation, v.kind) for v in violations] == [(f"{schema}.t6", "grant")]
    assert "service_role" in violations[0].detail


def test_check_rls_flags_grant_to_anon(
    check_rls: ModuleType, local_db_conn: psycopg.Connection[Any], schema: str
) -> None:
    local_db_conn.execute(f"create table {schema}.t4 (id int)")
    local_db_conn.execute(f"alter table {schema}.t4 enable row level security")
    local_db_conn.execute(f"grant select on table {schema}.t4 to anon")

    violations = check_rls.find_violations(local_db_conn, schema=schema)

    assert [(v.relation, v.kind) for v in violations] == [(f"{schema}.t4", "grant")]
    assert "SELECT" in violations[0].detail
    assert "anon" in violations[0].detail


def test_check_rls_flags_view_granted_to_authenticated(
    check_rls: ModuleType, local_db_conn: psycopg.Connection[Any], schema: str
) -> None:
    local_db_conn.execute(f"create view {schema}.v as select 1 as one")
    local_db_conn.execute(f"grant select on {schema}.v to authenticated")

    violations = check_rls.find_violations(local_db_conn, schema=schema)

    assert [(v.relation, v.kind) for v in violations] == [(f"{schema}.v", "grant")]
    assert "authenticated" in violations[0].detail


def test_check_rls_results_sorted_by_relation_then_kind(
    check_rls: ModuleType, local_db_conn: psycopg.Connection[Any], schema: str
) -> None:
    # b 沒開 RLS 且 grant 給 anon；a 只有 policy 給 anon
    # → a 先於 b，b 內 grant 先於 rls_disabled（依 relation、kind 排序）
    local_db_conn.execute(f"create table {schema}.b (id int)")
    local_db_conn.execute(f"grant select on table {schema}.b to anon")
    local_db_conn.execute(f"create table {schema}.a (id int)")
    local_db_conn.execute(f"alter table {schema}.a enable row level security")
    local_db_conn.execute(f"create policy p on {schema}.a for select to anon using (true)")

    violations = check_rls.find_violations(local_db_conn, schema=schema)

    assert [(v.relation, v.kind) for v in violations] == [
        (f"{schema}.a", "policy"),
        (f"{schema}.b", "grant"),
        (f"{schema}.b", "rls_disabled"),
    ]


def test_check_rls_main_exit_codes(
    check_rls: ModuleType,
    local_db_conn: psycopg.Connection[Any],
    local_db_url: str,
    committed_schema: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    local_db_conn.execute(f"create table {committed_schema}.ok (id int)")
    local_db_conn.execute(f"alter table {committed_schema}.ok enable row level security")
    local_db_conn.commit()

    assert check_rls.main(["--db-url", local_db_url, "--schema", committed_schema]) == 0
    assert "1" in capsys.readouterr().out

    local_db_conn.execute(f"create table {committed_schema}.t1 (id int)")
    local_db_conn.commit()

    assert check_rls.main(["--db-url", local_db_url, "--schema", committed_schema]) == 1
    out = capsys.readouterr().out
    assert "t1" in out
    assert "rls_disabled" in out


def test_check_rls_main_empty_schema(
    check_rls: ModuleType,
    local_db_url: str,
    committed_schema: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert check_rls.main(["--db-url", local_db_url, "--schema", committed_schema]) == 2
    assert "沒有任何表" in capsys.readouterr().err

    assert (
        check_rls.main(["--db-url", local_db_url, "--schema", committed_schema, "--allow-empty"])
        == 0
    )


def test_check_rls_main_rejects_remote_url(
    check_rls: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[object] = []

    def _record_connect(*args: object, **kwargs: object) -> None:
        calls.append((args, kwargs))
        raise AssertionError("不應連線")

    monkeypatch.setattr(check_rls.psycopg, "connect", _record_connect)

    code = check_rls.main(["--db-url", "postgresql://u:p@db.example.com:5432/postgres"])

    assert code == 2
    assert "--allow-remote" in capsys.readouterr().err
    assert calls == []


def test_check_rls_main_connection_failure(
    check_rls: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    # 54300 沒有服務在聽（本專案 port 從 54340 起）
    code = check_rls.main(["--db-url", "postgresql://postgres:postgres@127.0.0.1:54300/postgres"])

    assert code == 2
    assert "just db-start" in capsys.readouterr().err
