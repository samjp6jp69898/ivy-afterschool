"""seed_exam_types：預設考試類型（data migration，domain_spec M2；DB-037）。

衝突目標為 DB-009 uq_exam_types_name 的運算式 lower(btrim(name))：冪等，且後台已建同名類型時不重複
插入。sort_order 以 10 為間隔。測試以同一個 SEED_SQL 常數重跑驗證冪等。

Revision ID: db037
Revises: db036
Create Date: 2026-10-04 14:20:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "db037"
down_revision: str | Sequence[str] | None = "db036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SEED_SQL = """
insert into public.exam_types (name, sort_order, is_active) values
    ('段考', 10, true),
    ('小考', 20, true),
    ('複習考', 30, true)
on conflict ((lower(btrim(name)))) do nothing
"""


def upgrade() -> None:
    op.execute(SEED_SQL)


def downgrade() -> None:
    raise NotImplementedError("forward-only：以新的 revision 修正，不回滾")
