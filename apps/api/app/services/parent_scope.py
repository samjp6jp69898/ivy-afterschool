"""BACKEND-179：家長可見學生範圍 ``get_parent_student_ids``（domain_spec M3）。

移植 ivy ``api/parent_portal/_shared.py::_get_parent_student_ids``；去掉 tenant 與 guardian_ids
回傳。

- 可見 = ``guardians.parent_account_id = 自己`` 且 guardian、student 皆未封存；學生 ``withdrawn``
  仍可見（看歷史），寫入類操作另以 BACKEND-180 的 ``for_write`` 擋。
- 同一學生多位監護人綁同一家長時去重；依學生姓名、student_no 排序，家長端小孩切換順序穩定。
- 每次呼叫都查 DB（不快取），解除綁定立即生效。這是家長端 IDOR 防護（BACKEND-180）的根基。
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.parents import Guardian
from app.models.students import Student


def get_parent_student_ids(session: Session, parent_id: UUID) -> list[UUID]:
    # distinct 的 order by 欄位必須在 select 清單內，故一併選出 name / student_no 再只取 id
    stmt = (
        select(Student.id, Student.name, Student.student_no)
        .join(Guardian, Guardian.student_id == Student.id)
        .where(
            Guardian.parent_account_id == parent_id,
            Guardian.archived_at.is_(None),
            Student.archived_at.is_(None),
        )
        .distinct()
        .order_by(Student.name, Student.student_no, Student.id)
    )
    return [row.id for row in session.execute(stmt)]
