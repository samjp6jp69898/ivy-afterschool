"""BACKEND-304：app/jobs/attendance_jobs.py（每日出勤初始化背景工作 attendance.daily_init）。"""

from collections.abc import Iterator
from datetime import UTC, date, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

import app.jobs  # noqa: F401  觸發 @scheduled_job 註冊
from app.core.scheduler import JOB_REGISTRY, run_job_once
from app.models.attendance import StudentAttendance
from app.services.settings_service import clear_settings_cache
from tests.integration.db.conftest import connect_owner
from tests.support.factories import make_student
from tests.support.fake_clock import FakeClock

JOB_ID = "attendance.daily_init"


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    clear_settings_cache()
    yield
    clear_settings_cache()


@pytest.fixture
def owner_cleanup_students() -> Iterator[list[UUID]]:
    """committing 測試建立的學生以 owner 連線刪除；排在 committing_db_session 之前
    （先 close session、truncate 出勤表，再刪學生）。"""
    ids: list[UUID] = []
    yield ids
    with connect_owner() as conn:
        conn.execute("set lock_timeout = '5s'")
        for student_id in ids:
            conn.execute("delete from public.students where id = %s", (student_id,))
        conn.commit()


def test_attendance_job_registered() -> None:
    spec = JOB_REGISTRY[JOB_ID]

    assert isinstance(spec.trigger, CronTrigger)
    fields = {field.name: str(field) for field in spec.trigger.fields}
    assert (fields["minute"], fields["hour"]) == ("0,30", "5-20")
    assert spec.trigger.timezone == ZoneInfo("Asia/Taipei")


@pytest.mark.cleanup_tables("student_attendances")
def test_attendance_job_runs_and_idempotent(
    owner_cleanup_students: list[UUID], committing_db_session: Session, db_engine: Engine
) -> None:
    students = [make_student(committing_db_session) for _ in range(2)]
    committing_db_session.commit()
    owner_cleanup_students.extend(s.id for s in students)
    clock = FakeClock(datetime(2026, 9, 1, 0, 0, tzinfo=UTC))  # 台北 08:00
    spec = JOB_REGISTRY[JOB_ID]

    def count() -> int:
        with Session(bind=db_engine) as other:
            return other.execute(
                select(func.count())
                .select_from(StudentAttendance)
                .where(StudentAttendance.service_date == date(2026, 9, 1))
            ).scalar_one()

    assert run_job_once(spec, session_factory=lambda: Session(bind=db_engine), clock=clock) == "ran"
    assert count() == 2
    assert run_job_once(spec, session_factory=lambda: Session(bind=db_engine), clock=clock) == "ran"
    assert count() == 2


def test_attendance_job_run_key_taipei() -> None:
    spec = JOB_REGISTRY[JOB_ID]

    assert spec.run_key(FakeClock(datetime(2026, 9, 1, 16, 30, tzinfo=UTC))) == "2026-09-02"
    assert spec.run_key(FakeClock(datetime(2026, 9, 1, 15, 59, tzinfo=UTC))) == "2026-09-01"
