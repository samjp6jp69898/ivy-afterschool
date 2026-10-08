"""BACKEND-179：家長可見學生範圍 ``get_parent_student_ids``（domain_spec M3）。

移植 ivy ``api/parent_portal/_shared.py::_get_parent_student_ids``；去掉 tenant 與 guardian_ids
回傳。

- 可見 = ``guardians.parent_account_id = 自己`` 且 guardian、student 皆未封存；學生 ``withdrawn``
  與 ``suspended`` 仍可見（看歷史），寫入類操作另以 BACKEND-180 的 ``for_write`` 擋。
- 同一學生多位監護人綁同一家長時去重；依學生姓名、student_no 排序，家長端小孩切換順序穩定。
- 每次呼叫都查 DB（不快取），解除綁定立即生效。這是家長端 IDOR 防護（BACKEND-180）的根基。

BACKEND-180：``assert_parent_owns_student``（architecture_decisions §6；移植 ivy
``api/parent_portal/_shared.py::_assert_student_owned``，ivy 回 403 改為 404）。

- 不在可見範圍（他人小孩、不存在、封存學生 / 封存 guardian）一律同一個 404 ``student_not_found``，
  不洩漏學生是否存在。
- ``for_write=True`` 且學生不是 ``active``（``withdrawn`` 退班、``suspended`` 暫停，BACKEND-557）→
  409 ``student_not_active``，訊息依狀態區分（讀歷史仍允許）；只有確定是自己小孩後才檢查，他人的
  退班 / 暫停學生仍是 404。
- 家長端所有帶 ``student_id`` 的 endpoint 必經本函式（path 參數走 ``api/deps.py`` 的
  ``get_owned_student`` / ``get_owned_student_for_write``，body 內的 student_id 由 service 呼叫）。
"""

from __future__ import annotations

from typing import Final
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError, NotFoundError
from app.models.parents import Guardian
from app.models.students import Student

_INACTIVE_MESSAGES: Final = {
    "withdrawn": "此學生已退班，無法進行此操作",
    "suspended": "此學生目前暫停，無法進行此操作",
}
_INACTIVE_FALLBACK: Final = "此學生目前不在學，無法進行此操作"


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


def assert_parent_owns_student(
    session: Session, parent_id: UUID, student_id: UUID, *, for_write: bool = False
) -> Student:
    if student_id not in get_parent_student_ids(session, parent_id):
        raise NotFoundError("student_not_found", "找不到學生")
    student = session.execute(select(Student).where(Student.id == student_id)).scalar_one()
    if for_write and student.status != "active":
        message = _INACTIVE_MESSAGES.get(student.status, _INACTIVE_FALLBACK)
        raise AppError("student_not_active", message, status=409)
    return student
