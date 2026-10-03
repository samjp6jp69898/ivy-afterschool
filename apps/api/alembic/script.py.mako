<%!
    def q(value):
        """以雙引號輸出 revision 識別（符合 ruff format），None 原樣輸出。"""
        if value is None:
            return "None"
        if isinstance(value, str):
            return '"%s"' % value
        return "(" + ", ".join('"%s"' % v for v in value) + ",)"

    def revises(value):
        if value is None:
            return ""
        return " " + (value if isinstance(value, str) else ", ".join(value))
%>"""${message}

Revision ID: ${up_revision}
Revises:${revises(down_revision)}
Create Date: ${create_date}
"""

from collections.abc import Sequence

from alembic import op

revision: str = ${q(up_revision)}
down_revision: str | Sequence[str] | None = ${q(down_revision)}
branch_labels: str | Sequence[str] | None = ${q(branch_labels)}
depends_on: str | Sequence[str] | None = ${q(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    raise NotImplementedError("forward-only：以新的 revision 修正，不回滾")
