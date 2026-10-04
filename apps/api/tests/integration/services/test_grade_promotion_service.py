"""BACKEND-157：app/services/grade_promotion_service.py（preview，學年升級預覽）。

對象：未封存且 status ∈ {active, suspended}；1~5 年級 promote（grade +1）、6 年級 graduate；
依 grade、student_no 排序；class_id 不變（class_name 顯示原班）；已執行過該學年度升級 →
already_promoted。
"""

from datetime import date

from sqlalchemy.orm import Session

from app.services import audit_service
from app.services.grade_promotion_service import (
    PromotionItem,
    PromotionPreview,
    is_already_promoted,
    preview,
)
from tests.support.factories import make_class, make_student


def test_promotion_preview_classify(db_session: Session) -> None:
    klass = make_class(db_session, name="彩虹班")
    first = make_student(db_session, name="王小明", grade_level=1, class_=klass)
    fifth = make_student(db_session, name="陳小華", grade_level=5, status="suspended")
    sixth = make_student(db_session, name="林小安", grade_level=6)
    # DB CHECK：withdrawn 必須有 withdrawn_on，先建 active 再轉
    withdrawn = make_student(db_session, name="張小芳", grade_level=3)
    withdrawn.status = "withdrawn"
    withdrawn.withdrawn_on = date(2026, 7, 31)
    make_student(db_session, name="李小兵", grade_level=2, archived=True)
    db_session.flush()

    result = preview(db_session, from_academic_year=115)

    assert isinstance(result, PromotionPreview)
    assert result.from_academic_year == 115
    assert result.to_academic_year == 116
    assert [(i.grade_from, i.grade_to) for i in result.promote] == [(1, 2), (5, 6)]
    assert [i.id for i in result.promote] == [first.id, fifth.id]
    assert result.promote[0] == PromotionItem(
        id=first.id,
        student_no=first.student_no,
        name="王小明",
        grade_from=1,
        grade_to=2,
        class_name="彩虹班",
    )
    assert result.promote[1].class_name is None
    assert [i.id for i in result.graduate] == [sixth.id]
    assert result.graduate[0].grade_from == 6
    assert result.graduate[0].grade_to is None
    assert result.total == 3
    assert result.total == len(result.promote) + len(result.graduate)
    assert result.already_promoted is False


def test_promotion_preview_sorted_by_grade_then_student_no(db_session: Session) -> None:
    b = make_student(db_session, name="乙", grade_level=2, student_no="S-PRM-002")
    a = make_student(db_session, name="甲", grade_level=2, student_no="S-PRM-001")
    c = make_student(db_session, name="丙", grade_level=1, student_no="S-PRM-003")
    g2 = make_student(db_session, name="戊", grade_level=6, student_no="S-PRM-006")
    g1 = make_student(db_session, name="丁", grade_level=6, student_no="S-PRM-005")

    result = preview(db_session, from_academic_year=115)

    # 其他測試資料已 rollback，DB 內只有本測試的學生
    assert [i.id for i in result.promote] == [c.id, a.id, b.id]
    assert [i.id for i in result.graduate] == [g1.id, g2.id]
    assert result.total == 5


def test_promotion_preview_already_flag(db_session: Session) -> None:
    make_student(db_session, name="王小明", grade_level=1)
    audit_service.record(
        db_session,
        actor=audit_service.Actor.system(),
        action="student.promote_grade",
        entity_type="academic_year",
        entity_id="114",
        after={"promoted": 1, "graduated": 0, "to_academic_year": 115},
    )

    assert is_already_promoted(db_session, 114) is True
    assert preview(db_session, from_academic_year=114).already_promoted is True
    assert preview(db_session, from_academic_year=115).already_promoted is False
    # 仍回結果讓前端提示
    assert preview(db_session, from_academic_year=114).total == 1
