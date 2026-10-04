"""CI workflow 結構測試（INFRA-022 起，後續每個 CI job task 在此檔加自己的測試）。

helper：
- `workflow()`：載入 .github/workflows/ci.yml。
- `job(name)`：取得指定 job 的 dict。
- `run_lines(name)`：展開該 job 所有 step 的 `run`（多行 run 拆成逐行、去掉前後空白）。
"""

from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"
SETUP_ACTION = REPO_ROOT / ".github" / "actions" / "setup" / "action.yml"


def _load(path: Path) -> dict[Any, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"{path} 不是 YAML mapping"
    return data


def workflow() -> dict[Any, Any]:
    return _load(CI_YML)


def triggers() -> dict[str, Any]:
    wf = workflow()
    # YAML 1.1 會把裸字 on 解析成布林 True
    on = wf.get("on", wf.get(True))
    assert isinstance(on, dict), "ci.yml 的 on 必須是 mapping"
    return on


def job(name: str) -> dict[str, Any]:
    jobs = workflow()["jobs"]
    assert name in jobs, f"ci.yml 沒有 job {name}（現有：{sorted(jobs)}）"
    result: dict[str, Any] = jobs[name]
    return result


def run_lines(name: str) -> list[str]:
    lines: list[str] = []
    for step in job(name).get("steps", []):
        run = step.get("run")
        if run:
            lines += [line.strip() for line in str(run).splitlines() if line.strip()]
    return lines


def jobs_without_valid_timeout(jobs: dict[str, Any]) -> list[str]:
    """回傳沒有 timeout-minutes 或其值不是正整數的 job 名稱。"""
    bad: list[str] = []
    for name, spec in jobs.items():
        value = spec.get("timeout-minutes")
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            bad.append(name)
    return bad


def test_ci_triggers() -> None:
    on = triggers()

    assert "pull_request" in on
    assert "push" in on
    assert on["push"]["branches"] == ["main"]
    for event in ("pull_request", "push"):
        assert on[event]["paths-ignore"] == ["docs/mockups/**"]


def test_ci_permissions_read_only() -> None:
    assert workflow()["permissions"] == {"contents": "read"}


def test_ci_triggers_concurrency_cancels_in_progress() -> None:
    assert workflow()["concurrency"] == {
        "group": "ci-${{ github.ref }}",
        "cancel-in-progress": True,
    }


def test_ci_validate_tasks_job() -> None:
    assert job("validate-tasks")["runs-on"] == "ubuntu-latest"
    assert "python3 scripts/validate_tasks.py ." in run_lines("validate-tasks")


def test_ci_every_job_has_timeout() -> None:
    assert jobs_without_valid_timeout(workflow()["jobs"]) == []

    constructed = {
        "ok": {"timeout-minutes": 5},
        "missing": {"runs-on": "ubuntu-latest"},
        "zero": {"timeout-minutes": 0},
        "text": {"timeout-minutes": "5"},
    }
    assert jobs_without_valid_timeout(constructed) == ["missing", "zero", "text"]


def test_ci_setup_action_inputs() -> None:
    action = _load(SETUP_ACTION)

    assert action["runs"]["using"] == "composite"
    inputs = action["inputs"]
    assert set(inputs) == {"python", "node"}
    assert inputs["python"]["default"] == "true"
    assert inputs["node"]["default"] == "true"
    assert "supabase" not in SETUP_ACTION.read_text(encoding="utf-8").lower()


@pytest.mark.parametrize(
    ("needle", "condition"),
    [
        ("uv sync --frozen --project apps/api", "inputs.python == 'true'"),
        ("pnpm install --frozen-lockfile", "inputs.node == 'true'"),
    ],
)
def test_ci_setup_action_steps_follow_inputs(needle: str, condition: str) -> None:
    steps = _load(SETUP_ACTION)["runs"]["steps"]
    matched = [s for s in steps if needle in str(s.get("run", ""))]

    assert len(matched) == 1
    assert matched[0]["if"] == condition


def steps_of(name: str) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = job(name).get("steps", [])
    return steps


def line_index(lines: list[str], *needles: str) -> int:
    """第一個同時含所有 needles 的行索引；找不到時直接讓測試失敗並印出所有行。"""
    for index, line in enumerate(lines):
        if all(needle in line for needle in needles):
            return index
    raise AssertionError(f"找不到同時含 {needles} 的行：{lines}")


def step_with_run(name: str, needle: str) -> dict[str, Any]:
    matched = [s for s in steps_of(name) if needle in str(s.get("run", ""))]
    assert len(matched) == 1, f"job {name} 含 {needle!r} 的 step 數量為 {len(matched)}"
    return matched[0]


def test_ci_lint_job_runs_ruff() -> None:
    lines = run_lines("lint")

    check = lines[line_index(lines, "ruff check", "apps/api", "scripts")]
    assert "--config apps/api/pyproject.toml" in check
    assert line_index(lines, "ruff format --check", "apps/api", "scripts") >= 0


def test_ci_lint_job_runs_eslint() -> None:
    step = step_with_run("lint", "eslint .")

    assert step["working-directory"] == "apps/web"


def test_ci_lint_job_uses_setup_action() -> None:
    assert [s for s in steps_of("lint") if s.get("uses") == "./.github/actions/setup"]
    assert job("lint")["timeout-minutes"] == 10


def test_ci_typecheck_job_runs_mypy() -> None:
    lines = run_lines("typecheck")

    assert line_index(lines, "mypy app") >= 0
    assert line_index(lines, "mypy scripts") >= 0
    assert step_with_run("typecheck", "mypy app")["working-directory"] == "apps/api"
    assert "working-directory" not in step_with_run("typecheck", "mypy scripts")


def test_ci_typecheck_job_runs_vue_tsc() -> None:
    assert "just web-typecheck" in run_lines("typecheck")
    assert job("typecheck")["timeout-minutes"] == 15


def test_ci_unit_job_runs_api_and_scripts() -> None:
    lines = run_lines("unit")

    api_line = lines[line_index(lines, "pytest", '-m "not integration"')]
    assert "scripts/tests" not in api_line
    assert step_with_run("unit", api_line)["working-directory"] == "apps/api"
    assert line_index(lines, "pytest scripts/tests") >= 0
    assert job("unit")["timeout-minutes"] == 15


def test_ci_unit_job_has_no_db() -> None:
    for line in run_lines("unit"):
        assert "docker compose" not in line
        assert "db-reset" not in line


def test_ci_integration_job_order() -> None:
    lines = run_lines("integration")

    up = line_index(lines, "docker compose", "up")
    reset = lines.index("just db-reset --yes")
    api_tests = line_index(lines, "pytest -m integration")
    assert up < reset < api_tests
    assert job("integration")["timeout-minutes"] == 25


def test_ci_integration_job_waits_for_services() -> None:
    lines = run_lines("integration")

    up_line = lines[line_index(lines, "docker compose", "up")]
    assert "--wait" in up_line
    assert up_line.split().count("db") == 1
    assert up_line.split().count("storage") == 1


def test_ci_integration_job_tears_down_always() -> None:
    last = steps_of("integration")[-1]

    assert last["if"] == "always()"
    assert "docker compose" in last["run"]
    assert "down" in last["run"]


def test_ci_integration_job_runs_scripts_integration() -> None:
    assert line_index(run_lines("integration"), "pytest scripts/tests -m integration") >= 0


def test_ci_db_checks_job_order() -> None:
    lines = run_lines("db-checks")

    reset = lines.index("just db-reset --yes")
    heads = line_index(lines, "alembic heads")
    drift = lines.index("just schema-drift")
    assert reset < heads < drift
    assert job("db-checks")["timeout-minutes"] == 20


def test_ci_db_checks_single_head() -> None:
    lines = run_lines("db-checks")

    heads_line = lines[line_index(lines, "alembic heads")]
    assert "wc -l" in heads_line
    assert "-eq 1" in heads_line


def test_ci_db_checks_job_tears_down_always() -> None:
    last = steps_of("db-checks")[-1]

    assert last["if"] == "always()"
    assert "docker compose" in last["run"]
    assert "down" in last["run"]


def test_ci_web_test_job_runs_vitest() -> None:
    step = step_with_run("web-test", "vitest run")

    assert step["working-directory"] == "apps/web"
    assert job("web-test")["timeout-minutes"] == 15


def test_ci_web_test_job_skips_python() -> None:
    setup = [s for s in steps_of("web-test") if s.get("uses") == "./.github/actions/setup"]

    assert len(setup) == 1
    assert setup[0]["with"]["python"] is False
