"""INFRA-011：FakeClock 測試替身與 fake_clock fixture（Asia/Taipei，含跨午夜覆寫）。"""

import json
import textwrap
import tomllib
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from tests.support.fake_clock import FakeClock

API_DIR = Path(__file__).resolve().parents[3]
ROOT_CONFTEST = API_DIR / "tests" / "conftest.py"
FAKE_CLOCK_MODULE = API_DIR / "tests" / "support" / "fake_clock.py"
PYPROJECT = API_DIR / "pyproject.toml"


def test_fake_clock_default_now(fake_clock: FakeClock) -> None:
    assert fake_clock.now() == datetime(2026, 9, 1, 1, 0, tzinfo=UTC)
    assert fake_clock.today() == date(2026, 9, 1)


@pytest.mark.clock("2026-09-01T16:30:00+00:00")
def test_fake_clock_marker_cross_midnight(fake_clock: FakeClock) -> None:
    assert fake_clock.today() == date(2026, 9, 2)


@pytest.mark.clock("2026-09-01T15:59:59+00:00")
def test_fake_clock_before_taipei_midnight(fake_clock: FakeClock) -> None:
    assert fake_clock.today() == date(2026, 9, 1)


def test_fake_clock_advance(fake_clock: FakeClock) -> None:
    fake_clock.advance(minutes=30)
    assert fake_clock.now() == datetime(2026, 9, 1, 1, 30, tzinfo=UTC)

    fake_clock.advance(timedelta(hours=15))
    assert fake_clock.now() == datetime(2026, 9, 1, 16, 30, tzinfo=UTC)
    assert fake_clock.today() == date(2026, 9, 2)


def test_fake_clock_rejects_naive(fake_clock: FakeClock) -> None:
    with pytest.raises(ValueError, match="aware"):
        FakeClock(datetime(2026, 9, 1, 9, 0))  # noqa: DTZ001  刻意用 naive datetime 驗證拒絕

    with pytest.raises(ValueError, match="aware"):
        fake_clock.set(datetime(2026, 9, 1))  # noqa: DTZ001

    # 拒絕後時間不變
    assert fake_clock.now() == datetime(2026, 9, 1, 1, 0, tzinfo=UTC)


def _pytest_ini_toml() -> str:
    options = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["tool"]["pytest"]["ini_options"]
    lines = ["[tool.pytest.ini_options]"]
    lines += [f"{key} = {json.dumps(value, ensure_ascii=False)}" for key, value in options.items()]
    return "\n".join(lines) + "\n"


def test_fake_clock_marker_requires_timezone(pytester: pytest.Pytester) -> None:
    pytester.makepyprojecttoml(_pytest_ini_toml())
    tests_dir = pytester.path / "tests"
    (tests_dir / "support").mkdir(parents=True)
    (tests_dir / "unit").mkdir()
    (tests_dir / "conftest.py").write_text(
        ROOT_CONFTEST.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (tests_dir / "support" / "fake_clock.py").write_text(
        FAKE_CLOCK_MODULE.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (tests_dir / "unit" / "test_naive_marker.py").write_text(
        textwrap.dedent(
            """
            import pytest

            @pytest.mark.clock("2026-09-01T09:00:00")
            def test_naive(fake_clock):
                assert fake_clock.today()
            """
        ),
        encoding="utf-8",
    )

    result = pytester.runpytest_subprocess()

    result.assert_outcomes(failed=0, errors=1)
    result.stdout.fnmatch_lines(["*clock marker 必須含時區*"])
