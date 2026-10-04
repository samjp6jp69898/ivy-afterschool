"""DB-036 / DB-037：預設科目與考試類型 seed（db036 / db037 的 SEED_SQL）。

只看 seed 的名稱，不假設表內沒有其他列；冪等測試以 owner（migration 的身分）在 transaction 內重跑
SEED_SQL，結束 rollback。
"""

from psycopg import sql

from tests.integration.db.conftest import Conn, load_seed_sql

SUBJECTS = ["國語", "數學", "英語", "自然", "社會"]
EXAM_TYPES = ["段考", "小考", "複習考"]


def _seeded(conn: Conn, table: str, names: list[str]) -> list[tuple[str, int, bool]]:
    query = sql.SQL(
        "select name, sort_order, is_active from {} where name = any (%s) order by sort_order, name"
    ).format(sql.Identifier("public", table))
    rows = conn.execute(query, (names,)).fetchall()
    return [(name, sort_order, is_active) for name, sort_order, is_active in rows]


def _count(conn: Conn, table: str) -> int:
    query = sql.SQL("select count(*) from {}").format(sql.Identifier("public", table))
    row = conn.execute(query).fetchone()
    assert row is not None
    count: int = row[0]
    return count


def test_seed_subjects_present_in_order(backend_conn: Conn) -> None:
    assert _seeded(backend_conn, "subjects", SUBJECTS) == [
        ("國語", 10, True),
        ("數學", 20, True),
        ("英語", 30, True),
        ("自然", 40, True),
        ("社會", 50, True),
    ]


def test_seed_subjects_idempotent(owner_conn: Conn) -> None:
    before = _count(owner_conn, "subjects")

    owner_conn.execute(load_seed_sql("db036_seed_subjects.py"))

    assert _count(owner_conn, "subjects") == before
    assert [name for name, _, _ in _seeded(owner_conn, "subjects", SUBJECTS)] == SUBJECTS


def test_seed_exam_types_present_in_order(backend_conn: Conn) -> None:
    assert _seeded(backend_conn, "exam_types", EXAM_TYPES) == [
        ("段考", 10, True),
        ("小考", 20, True),
        ("複習考", 30, True),
    ]


def test_seed_exam_types_idempotent(owner_conn: Conn) -> None:
    before = _count(owner_conn, "exam_types")

    owner_conn.execute(load_seed_sql("db037_seed_exam_types.py"))

    assert _count(owner_conn, "exam_types") == before
    assert [name for name, _, _ in _seeded(owner_conn, "exam_types", EXAM_TYPES)] == EXAM_TYPES
