"""INFRA-010：tests/integration/conftest.py 的 SQLAlchemy session fixture。

不依賴任何業務表：本模組以 owner 連線建立探針 schema `infra_probe`（只用於建立 / 刪除探針物件與驗證
清表結果），被測的 `db_session` / `committing_db_session` 一律以 app_backend 執行。

守衛類案例（遠端 URL、DB 未啟動、owner 角色、連線後位址、marker 檢查）以 pytester 子行程執行：
把真實的 tests/conftest.py、tests/support 與 tests/integration/conftest.py 複製進暫存專案，
子行程透過環境變數 TEST_DB_BACKEND_URL 指向要測的連線目標。
"""

import json
import textwrap
import tomllib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.support import db_urls

API_DIR = Path(__file__).resolve().parents[3]
TESTS_DIR = API_DIR / "tests"
PYPROJECT = API_DIR / "pyproject.toml"
OWNER_URL_FOR_BACKEND = "postgresql+psycopg://postgres:postgres@127.0.0.1:54342/postgres"
_LIBPQ_ADDRESS_ENV = ("PGHOST", "PGHOSTADDR", "PGSERVICE", "TEST_DB_BACKEND_URL")

Conn = psycopg.Connection[tuple[Any, ...]]


def _owner_connect() -> Conn:
    conn = psycopg.connect(db_urls.to_psycopg_dsn(db_urls.owner_url()), autocommit=True)
    db_urls.assert_connected_loopback(conn.info.hostaddr)
    return conn


def _probe_count() -> int:
    with _owner_connect() as conn:
        row = conn.execute("select count(*) from infra_probe.items").fetchone()
    assert row is not None
    return int(row[0])


@pytest.fixture(scope="module", autouse=True)
def infra_probe() -> Iterator[None]:
    with _owner_connect() as conn:
        conn.execute("drop schema if exists infra_probe cascade")
        conn.execute("create schema infra_probe")
        conn.execute("create table infra_probe.items (id serial primary key, name text)")
        conn.execute("grant usage on schema infra_probe to app_backend")
        conn.execute("grant all on infra_probe.items to app_backend")
        conn.execute("grant usage on sequence infra_probe.items_id_seq to app_backend")
    yield
    with _owner_connect() as conn:
        conn.execute("drop schema infra_probe cascade")


# ---------------------------------------------------------------------------
# db_session：被測程式碼 commit 也只釋放 savepoint，測試結束全部 rollback
# ---------------------------------------------------------------------------


def test_db_fixtures_rollback_even_after_commit_step1(db_session: Session) -> None:
    db_session.execute(text("insert into infra_probe.items (name) values ('王小明')"))
    db_session.commit()

    count = db_session.execute(text("select count(*) from infra_probe.items")).scalar_one()
    assert count == 1


def test_db_fixtures_rollback_even_after_commit_step2(db_session: Session) -> None:
    count = db_session.execute(text("select count(*) from infra_probe.items")).scalar_one()
    assert count == 0


def test_db_fixtures_session_is_app_backend(db_session: Session) -> None:
    assert db_session.execute(text("select current_user")).scalar() == "app_backend"


# ---------------------------------------------------------------------------
# committing_db_session：真 commit，測試結束由 owner 連線清表
# ---------------------------------------------------------------------------


@pytest.mark.cleanup_tables("infra_probe.items")
def test_db_fixtures_committing_session_truncates_after_step1(
    committing_db_session: Session,
) -> None:
    committing_db_session.execute(text("insert into infra_probe.items (name) values ('王小美')"))
    committing_db_session.commit()

    assert committing_db_session.execute(text("select current_user")).scalar() == "app_backend"
    assert _probe_count() == 1


def test_db_fixtures_committing_session_truncates_after_step2(db_session: Session) -> None:
    count = db_session.execute(text("select count(*) from infra_probe.items")).scalar_one()
    assert count == 0
    assert _probe_count() == 0


# ---------------------------------------------------------------------------
# pytester：守衛與 marker 檢查
# ---------------------------------------------------------------------------


def _pytest_ini_toml() -> str:
    options = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["tool"]["pytest"]["ini_options"]
    lines = ["[tool.pytest.ini_options]"]
    lines += [f"{key} = {json.dumps(value, ensure_ascii=False)}" for key, value in options.items()]
    return "\n".join(lines) + "\n"


def _copy(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")


@pytest.fixture
def project(pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch) -> pytest.Pytester:
    """複製真實 conftest 與 pytest 設定的暫存專案（子行程不繼承外層的 PG* 位址變數）。"""
    for name in _LIBPQ_ADDRESS_ENV:
        monkeypatch.delenv(name, raising=False)
    pytester.makepyprojecttoml(_pytest_ini_toml())
    _copy(TESTS_DIR / "conftest.py", pytester.path / "tests" / "conftest.py")
    for module in (TESTS_DIR / "support").glob("*.py"):
        _copy(module, pytester.path / "tests" / "support" / module.name)
    _copy(
        TESTS_DIR / "integration" / "conftest.py",
        pytester.path / "tests" / "integration" / "conftest.py",
    )
    return pytester


def _write(pytester: pytest.Pytester, rel_path: str, source: str) -> None:
    path = pytester.path / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(source), encoding="utf-8")


def _run(pytester: pytest.Pytester) -> pytest.RunResult:
    return pytester.runpytest_subprocess("-m", "integration", "-p", "no:cacheprovider")


def _output(result: pytest.RunResult) -> str:
    return "\n".join(result.outlines + result.errlines)


_TWO_DB_TESTS = """
def test_one(db_session):
    pass

def test_two(db_session):
    pass
"""


def test_db_fixtures_committing_session_requires_marker(project: pytest.Pytester) -> None:
    _write(
        project,
        "tests/integration/probe/test_probe.py",
        """
        def test_no_marker(committing_db_session):
            pass
        """,
    )

    result = _run(project)

    # fixture 在 setup 階段 pytest.fail，計為 error
    result.assert_outcomes(errors=1)
    assert "committing_db_session 必須搭配 cleanup_tables marker" in _output(result)


def _roles_count() -> int | None:
    with _owner_connect() as conn:
        exists = conn.execute("select to_regclass('public.roles') is not null").fetchone()
        assert exists is not None
        if not exists[0]:
            return None
        row = conn.execute("select count(*) from public.roles").fetchone()
    assert row is not None
    return int(row[0])


def test_db_fixtures_refuses_seed_table_cleanup(project: pytest.Pytester) -> None:
    before = _roles_count()
    _write(
        project,
        "tests/integration/probe/test_probe.py",
        """
        import pytest

        @pytest.mark.cleanup_tables("roles")
        def test_seed(committing_db_session):
            pass
        """,
    )

    result = _run(project)

    result.assert_outcomes(errors=1)
    assert "不可清空 seed 表：roles" in _output(result)
    assert _roles_count() == before


def test_db_fixtures_rejects_remote_url(
    project: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(
        "TEST_DB_BACKEND_URL", "postgresql+psycopg://u:p@db.example.com:5432/postgres"
    )
    _write(project, "tests/integration/probe/test_probe.py", _TWO_DB_TESTS)

    result = _run(project)

    assert result.ret == 2
    assert "只允許本機 loopback DB" in _output(result)
    assert result.parseoutcomes().get("passed", 0) == 0


def test_db_fixtures_db_down_exits_once(
    project: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(
        "TEST_DB_BACKEND_URL",
        "postgresql+psycopg://app_backend:app_backend_local@127.0.0.1:1/postgres",
    )
    _write(project, "tests/integration/probe/test_probe.py", _TWO_DB_TESTS)

    result = _run(project)

    assert result.ret == 2
    assert _output(result).count("just db-start") == 1
    assert result.parseoutcomes().get("passed", 0) == 0


def test_db_fixtures_refuses_owner_role(
    project: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TEST_DB_BACKEND_URL", OWNER_URL_FOR_BACKEND)
    _write(
        project,
        "tests/integration/probe/test_probe.py",
        """
        def test_owner(db_session):
            pass
        """,
    )

    result = _run(project)

    assert result.ret == 2
    output = _output(result)
    assert "禁止以 owner 角色繞過 RLS" in output
    assert "postgres" in output
    assert result.parseoutcomes().get("passed", 0) == 0


def test_db_fixtures_rejects_connected_non_loopback(project: pytest.Pytester) -> None:
    # 連線前的 URL 是 loopback（預設 app_backend URL），連線後的實際位址被改成 10.0.0.11
    _write(
        project,
        "tests/integration/probe/conftest.py",
        """
        import psycopg
        import pytest

        pytest.MonkeyPatch().setattr(
            psycopg.ConnectionInfo, "hostaddr", property(lambda self: "10.0.0.11")
        )
        """,
    )
    _write(
        project,
        "tests/integration/probe/test_probe.py",
        """
        def test_probe(db_session):
            pass
        """,
    )

    result = _run(project)

    assert result.ret == 2
    assert "10.0.0.11" in _output(result)
    assert result.parseoutcomes().get("passed", 0) == 0
