"""BACKEND-100：app/models/reference.py（SystemSetting、Subject、ExamType、School、ClosedDay）。

以 app_backend 的 db_session 寫入 / 讀回；seed 資料（DB-036 ~ DB-038）只讀不改。
"""

from datetime import date
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.reference import ClosedDay, ExamType, School, Subject, SystemSetting


def test_reference_models_defaults(db_session: Session) -> None:
    subject = Subject(name="書法")
    exam_type = ExamType(name="隨堂測驗（探針）")
    school = School(name="臺北市大安區新生國小", short_name="新生")
    db_session.add_all([subject, exam_type, school])
    db_session.flush()
    for row in (subject, exam_type, school):
        db_session.refresh(row)

    assert isinstance(subject.id, UUID)
    assert subject.is_active is True
    assert subject.sort_order == 0
    assert exam_type.is_active is True
    assert exam_type.sort_order == 0
    assert school.is_active is True
    assert school.created_at.tzinfo is not None

    db_session.expunge_all()
    loaded = db_session.execute(select(School).where(School.id == school.id)).scalar_one()
    assert (loaded.name, loaded.short_name, loaded.is_active) == (
        "臺北市大安區新生國小",
        "新生",
        True,
    )


def test_reference_models_setting_jsonb(db_session: Session) -> None:
    setting = db_session.execute(
        select(SystemSetting).where(SystemSetting.key == "org.profile")
    ).scalar_one()

    assert setting.value == {"name": "", "address": "", "phone": "", "logo_url": None}
    assert setting.is_secret is False
    assert setting.updated_by is None

    secret = db_session.execute(
        select(SystemSetting).where(SystemSetting.key == "line.messaging")
    ).scalar_one()
    assert secret.is_secret is True

    probe = SystemSetting(key="probe.setting", value={"n": 1, "nested": {"ok": True}})
    db_session.add(probe)
    db_session.flush()
    db_session.expunge_all()
    loaded = db_session.execute(
        select(SystemSetting).where(SystemSetting.key == "probe.setting")
    ).scalar_one()
    assert loaded.value == {"n": 1, "nested": {"ok": True}}
    assert loaded.is_secret is False


def test_reference_models_closed_day_unique(db_session: Session) -> None:
    first = ClosedDay(date=date(2026, 10, 10), reason="國慶日")
    db_session.add(first)
    db_session.flush()
    db_session.refresh(first)
    assert first.date == date(2026, 10, 10)
    assert first.reason == "國慶日"

    db_session.add(ClosedDay(date=date(2026, 10, 10)))
    with pytest.raises(IntegrityError) as excinfo:
        db_session.flush()
    assert getattr(excinfo.value.orig, "sqlstate", None) == "23505"
    db_session.rollback()


def test_reference_models_name_unique_case_insensitive(db_session: Session) -> None:
    """DB 的 functional unique index（lower(btrim(name))）在 ORM 寫入時同樣生效。"""
    db_session.add(Subject(name="Probe Subject"))
    db_session.flush()
    db_session.add(Subject(name="  probe subject "))
    with pytest.raises(IntegrityError) as excinfo:
        db_session.flush()
    assert getattr(excinfo.value.orig, "sqlstate", None) == "23505"
    db_session.rollback()
