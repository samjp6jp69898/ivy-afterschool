"""seed_subjects：預設科目（data migration，domain_spec M2；DB-036）。

衝突目標為 DB-008 uq_subjects_name 的運算式 lower(btrim(name))：冪等，且後台已建同名科目時不重複
插入。sort_order 以 10 為間隔，方便後台插入新科目。測試以同一個 SEED_SQL 常數重跑驗證冪等。

Revision ID: db036
Revises: db035
Create Date: 2026-10-04 14:10:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "db036"
down_revision: str | Sequence[str] | None = "db035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SEED_SQL = """
insert into public.subjects (name, sort_order, is_active) values
    ('國語', 10, true),
    ('數學', 20, true),
    ('英語', 30, true),
    ('自然', 40, true),
    ('社會', 50, true)
on conflict ((lower(btrim(name)))) do nothing
"""


def upgrade() -> None:
    op.execute(SEED_SQL)


def downgrade() -> None:
    raise NotImplementedError("forward-only：以新的 revision 修正，不回滾")
