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
    assert {"python", "node", "supabase"} <= set(inputs)
    assert inputs["python"]["default"] == "true"
    assert inputs["node"]["default"] == "true"
    assert inputs["supabase"]["default"] == "false"


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
