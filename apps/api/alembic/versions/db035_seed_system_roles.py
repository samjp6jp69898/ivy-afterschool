"""seed_system_roles：系統預設角色與權限碼（data migration，domain_spec §3；DB-035）。

admin 存萬用碼 `*`（全部權限碼，含未來新增；後端計算有效權限時展開成 Permission enum 全部值），
students:purge 只經由它取得。director / clerk / tutor 逐碼列出，權限碼字串與 domain_spec §3、
app/core/permissions.py 的 Permission enum 一致。

`on conflict (code) do nothing`：冪等，且不覆寫日後在後台調整過的角色權限。測試以同一個
SEED_SQL 常數重跑驗證冪等。

Revision ID: db035
Revises: db029
Create Date: 2026-10-04 14:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "db035"
down_revision: str | Sequence[str] | None = "db029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SEED_SQL = """
insert into public.roles (code, name, description, permissions, is_system) values
(
    'admin',
    '系統管理員',
    '系統管理員，擁有全部權限，包含員工帳號、角色權限與永久刪除退班學生',
    array['*'],
    true
),
(
    'director',
    '主任',
    '主任，可管理學生、出勤、成績與系統設定，不可管理員工帳號與角色',
    array[
        'dashboard:read', 'staff:read', 'roles:read', 'settings:read', 'settings:write',
        'audit:read', 'classes:read', 'classes:write', 'students:read', 'students:write',
        'students:sensitive', 'guardians:write', 'attendance:read', 'attendance:operate',
        'attendance:amend', 'leaves:read', 'leaves:write', 'homework:read', 'homework:write',
        'pickup:read', 'pickup:operate', 'pickup:override', 'exams:read', 'exams:write',
        'exams:publish'
    ],
    true
),
(
    'clerk',
    '行政',
    '行政人員，可維護學生、班級、監護人與請假，處理出勤、接送並發布成績',
    array[
        'dashboard:read', 'settings:read', 'classes:read', 'classes:write', 'students:read',
        'students:write', 'guardians:write', 'attendance:read', 'attendance:operate',
        'leaves:read', 'leaves:write', 'homework:read', 'homework:write', 'pickup:read',
        'pickup:operate', 'exams:read', 'exams:write', 'exams:publish'
    ],
    true
),
(
    'tutor',
    '課輔老師',
    '課輔老師，可登記出勤、更新作業進度、處理接送與輸入成績',
    array[
        'dashboard:read', 'classes:read', 'students:read', 'attendance:read',
        'attendance:operate', 'leaves:read', 'homework:read', 'homework:write', 'pickup:read',
        'pickup:operate', 'exams:read', 'exams:write'
    ],
    true
)
on conflict (code) do nothing
"""


def upgrade() -> None:
    op.execute(SEED_SQL)


def downgrade() -> None:
    raise NotImplementedError("forward-only：以新的 revision 修正，不回滾")
