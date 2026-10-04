"""SQLAlchemy models。

re-export ``Base``；各模組 models task 在此加入 import 行，讓 ``Base.metadata`` 完整。
INFRA-020 的 ``scripts/check_schema_drift.py`` 以 ``from app.models import Base`` 取得 metadata。
"""

from app.models import account, audit, classes, parents, reference, students
from app.models.base import Base

__all__ = ["Base", "account", "audit", "classes", "parents", "reference", "students"]
