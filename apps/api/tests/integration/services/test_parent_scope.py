"""BACKEND-179：app/services/parent_scope.py（get_parent_student_ids，家長可見學生範圍）。

domain_spec M3：``guardians.parent_account_id = 自己`` 且 guardian / student 皆未封存；withdrawn
仍可見；每次呼叫都查 DB。這是家長端 IDOR 防護（BACKEND-180）的根基。
"""

from datetime import date
from uuid import uuid4

from sqlalchemy.orm import Session

from app.services.parent_scope import get_parent_student_ids
from tests.support.factories import make_guardian, make_parent, make_student


def test_parent_student_ids_basic(db_session: Session) -> None:
    p = make_parent(db_session)
    ming = make_student(db_session, name="王小明")
    hua = make_student(db_session, name="陳小華")
    an = make_student(db_session, name="林小安", archived=True)
    make_guardian(db_session, ming, parent=p)
    make_guardian(db_session, hua, parent=p, archived=True)
    make_guardian(db_session, an, parent=p)
    # 未綁定家長帳號的監護人不算
    make_guardian(db_session, make_student(db_session, name="張小芳"), parent=None)

    assert get_parent_student_ids(db_session, p.id) == [ming.id]


def test_parent_student_ids_withdrawn_visible(db_session: Session) -> None:
    p = make_parent(db_session)
    ming = make_student(db_session, name="王小明")
    make_guardian(db_session, ming, parent=p)
    # DB CHECK：withdrawn 必須有 withdrawn_on
    ming.status = "withdrawn"
    ming.withdrawn_on = date(2026, 7, 31)
    db_session.flush()

    assert get_parent_student_ids(db_session, p.id) == [ming.id]


def test_parent_student_ids_isolation(db_session: Session) -> None:
    p = make_parent(db_session)
    q = make_parent(db_session)
    ming = make_student(db_session, name="王小明")
    hua = make_student(db_session, name="陳小華")
    make_guardian(db_session, ming, parent=p)
    make_guardian(db_session, hua, parent=q)

    assert get_parent_student_ids(db_session, p.id) == [ming.id]
    assert get_parent_student_ids(db_session, q.id) == [hua.id]
    assert get_parent_student_ids(db_session, uuid4()) == []


def test_parent_student_ids_ordered_and_distinct(db_session: Session) -> None:
    """同一學生有舊的（已封存）與現行的綁定時只回一次；依姓名、student_no 排序。

    同一家長對同一學生的未封存綁定由 DB partial unique index 擋，重複只可能來自封存列。
    """
    p = make_parent(db_session)
    b1 = make_student(db_session, name="李小兵", student_no="S-ORD-002")
    b0 = make_student(db_session, name="李小兵", student_no="S-ORD-001")
    a = make_student(db_session, name="丁小安", student_no="S-ORD-009")
    make_guardian(db_session, b1, parent=p, name="李爸爸", relation="father", archived=True)
    make_guardian(db_session, b1, parent=p, name="李媽媽", relation="mother")
    make_guardian(db_session, b0, parent=p)
    make_guardian(db_session, a, parent=p)

    assert get_parent_student_ids(db_session, p.id) == [a.id, b0.id, b1.id]


def test_parent_student_ids_unbind_immediately(db_session: Session) -> None:
    """解除綁定（guardian.parent_account_id 清空或封存）立即生效，不快取。"""
    p = make_parent(db_session)
    ming = make_student(db_session, name="王小明")
    g = make_guardian(db_session, ming, parent=p)
    assert get_parent_student_ids(db_session, p.id) == [ming.id]

    g.parent_account_id = None
    db_session.flush()
    assert get_parent_student_ids(db_session, p.id) == []
