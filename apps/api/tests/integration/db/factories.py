"""DB 整合測試的測資 factory（DB-002）。

各表 task 在此補自己的 `make_<table>(conn, **overrides)`，預設值用擬真假資料
（學生「王小明」、電話 `0912-000-001`），寫入一律經 `backend_conn`。
"""

from datetime import date
from typing import Any

import psycopg
from psycopg import sql
from psycopg.rows import dict_row


def split_table(table: str) -> tuple[str, str]:
    """`schema.name` 拆成 (schema, name)；未帶 schema 時為 `public`。"""
    schema, _, name = table.rpartition(".")
    return schema or "public", name


def insert_row(conn: psycopg.Connection[Any], table: str, **cols: Any) -> dict[str, Any]:
    """`insert into <table> (...) values (...) returning *`，回傳插入後的整列。

    `table` 可寫 `schema.name`，未帶 schema 時為 `public`；沒有欄位時以 default values 插入。
    """
    target = sql.Identifier(*split_table(table))
    if cols:
        query = sql.SQL("insert into {} ({}) values ({}) returning *").format(
            target,
            sql.SQL(", ").join(sql.Identifier(col) for col in cols),
            sql.SQL(", ").join(sql.Placeholder() for _ in cols),
        )
    else:
        query = sql.SQL("insert into {} default values returning *").format(target)
    with conn.cursor(row_factory=dict_row) as cur:
        row = cur.execute(query, list(cols.values())).fetchone()
    assert row is not None, f"insert into {table} 沒有回傳列"
    return row


# 各表 factory：預設值避開 data migration seed 的值（DB-035~037），可直接在已 seed 的 DB 上插入


def make_roles(conn: psycopg.Connection[Any], **overrides: Any) -> dict[str, Any]:
    return insert_row(
        conn, "public.roles", **{"code": "counselor", "name": "輔導老師", **overrides}
    )


def make_subjects(conn: psycopg.Connection[Any], **overrides: Any) -> dict[str, Any]:
    return insert_row(conn, "public.subjects", **{"name": "書法", **overrides})


def make_exam_types(conn: psycopg.Connection[Any], **overrides: Any) -> dict[str, Any]:
    return insert_row(conn, "public.exam_types", **{"name": "週考", **overrides})


def make_schools(conn: psycopg.Connection[Any], **overrides: Any) -> dict[str, Any]:
    return insert_row(conn, "public.schools", **{"name": "臺北市大安區新生國小", **overrides})


def make_closed_days(conn: psycopg.Connection[Any], **overrides: Any) -> dict[str, Any]:
    return insert_row(
        conn, "public.closed_days", **{"date": date(2026, 10, 10), "reason": "國慶日", **overrides}
    )


def make_classes(conn: psycopg.Connection[Any], **overrides: Any) -> dict[str, Any]:
    defaults = {"name": "低年級 A 班", "grade_levels": [1, 2], "academic_year": 115}
    return insert_row(conn, "public.classes", **{**defaults, **overrides})
