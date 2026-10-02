"""根 conftest：測試分層的強制機制（docs/testing_conventions.md §3）與時鐘注入，不放業務 fixture。

1. tests/unit/** 自動加 unit marker；tests/integration/** 自動加 integration marker。
2. 測試檔不在 tests/unit/ 或 tests/integration/ 底下 → collection error。
3. tests/unit/** 自行標 integration（或 tests/integration/** 標 unit）→ collection error。
4. unit 測試完全禁網（含 loopback，unix socket 仍允許）；integration 只放行 loopback。
5. `fake_clock` fixture（INFRA-011）：預設 2026-09-01T09:00:00+08:00，
   `@pytest.mark.clock("<ISO8601 含時區>")` 覆寫起始時間。

業務 fixture 由各子目錄的 conftest 提供。
"""

from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

import pytest

from tests.support.fake_clock import FakeClock

pytest_plugins = ["pytester"]

_DEFAULT_CLOCK_START = "2026-09-01T09:00:00+08:00"

_TESTS_DIR = Path(__file__).resolve().parent
_LAYERS = ("unit", "integration")
_LOOPBACK_HOSTS = ["127.0.0.1", "::1"]


def _layer_of(path: Path) -> str | None:
    resolved = path.resolve()
    for layer in _LAYERS:
        if resolved.is_relative_to(_TESTS_DIR / layer):
            return layer
    return None


class _MisplacedTestFile(pytest.File):
    """取代分層外測試檔的 Module：收集時直接報錯，檔內測試一個都不執行。"""

    def collect(self) -> Iterable[pytest.Item | pytest.Collector]:
        raise self.CollectError(
            "測試檔必須放在 tests/unit/ 或 tests/integration/："
            f"{self.path.relative_to(_TESTS_DIR.parent)}"
        )


def pytest_pycollect_makemodule(module_path: Path, parent: pytest.Collector) -> pytest.File | None:
    resolved = module_path.resolve()
    if resolved.is_relative_to(_TESTS_DIR) and _layer_of(resolved) is None:
        return _MisplacedTestFile.from_parent(parent, path=module_path)
    return None


def _report_collect_error(item: pytest.Item, message: str) -> None:
    report = pytest.CollectReport(nodeid=item.nodeid, outcome="failed", longrepr=message, result=[])
    item.ihook.pytest_collectreport(report=report)


# tryfirst：必須在 -m 依 marker 篩選（deselect）之前補上 marker 並檢查錯標
@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    kept: list[pytest.Item] = []
    for item in items:
        layer = _layer_of(item.path)
        if layer is None:
            kept.append(item)
            continue
        other = "integration" if layer == "unit" else "unit"
        if item.get_closest_marker(other) is not None:
            # 錯標的測試不執行（即使帶 --continue-on-collection-errors）
            _report_collect_error(item, f"tests/{layer} 底下不可標 {other}：{item.nodeid}")
            continue
        item.add_marker(layer)
        if layer == "unit":
            item.add_marker(pytest.mark.disable_socket)
        else:
            item.add_marker(pytest.mark.allow_hosts(_LOOPBACK_HOSTS))
        kept.append(item)
    items[:] = kept


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "clock(iso8601): 覆寫 fake_clock 的起始時間（ISO 8601，必須含時區）"
    )


@pytest.fixture
def fake_clock(request: pytest.FixtureRequest) -> FakeClock:
    marker = request.node.get_closest_marker("clock")
    raw = _DEFAULT_CLOCK_START if marker is None else str(marker.args[0])
    try:
        start = datetime.fromisoformat(raw)
    except ValueError:
        pytest.fail(f"clock marker 不是合法的 ISO 8601：{raw!r}")
    if start.tzinfo is None:
        pytest.fail(f"clock marker 必須含時區：{raw!r}")
    return FakeClock(start)
