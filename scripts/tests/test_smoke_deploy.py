"""INFRA-039：scripts/smoke_deploy.sh 的行為測試。

以 http.server.ThreadingHTTPServer 綁 127.0.0.1 臨時 port，依各測試設定的回應表回傳狀態碼與標頭，
並記錄收到的請求路徑。
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from conftest import RunCmd

pytestmark = pytest.mark.unit

SCRIPT = Path(__file__).resolve().parents[1] / "smoke_deploy.sh"
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
}
SPA: tuple[int, dict[str, str], str] = (200, {}, '<html><div id="app"></div></html>')


def _all_pass_routes() -> dict[str, tuple[int, dict[str, str], str]]:
    return {
        "/healthz": (200, {}, "ok"),
        "/": (200, dict(SECURITY_HEADERS), '<html><div id="app"></div></html>'),
        "/parent/": SPA,
        "/api/health": (200, {}, '{"status":"ok"}'),
        "/api/admin/auth/me": (401, {}, '{"detail":"unauthorized"}'),
    }


class FakeServer:
    def __init__(self) -> None:
        self.routes = _all_pass_routes()
        self.requests: list[str] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                outer.requests.append(self.path)
                status, headers, body = outer.routes.get(self.path, (404, {}, "not found"))
                payload = body.encode()
                self.send_response(status)
                for name, value in headers.items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format: str, *args: object) -> None:
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self) -> FakeServer:
        self.thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server() -> Iterator[FakeServer]:
    with FakeServer() as fake:
        yield fake


def _fail_lines(stdout: str) -> list[str]:
    return [line for line in stdout.splitlines() if line.startswith("FAIL")]


def test_smoke_all_pass(server: FakeServer, run_cmd: RunCmd) -> None:
    result = run_cmd(["bash", str(SCRIPT), server.url])

    assert result.returncode == 0, result.stdout + result.stderr
    assert "FAIL" not in result.stdout
    assert result.stdout.count("OK") == 7
    assert any(path.endswith(".js.map") for path in server.requests)


def test_smoke_missing_csp(server: FakeServer, run_cmd: RunCmd) -> None:
    headers = dict(SECURITY_HEADERS)
    del headers["Content-Security-Policy"]
    server.routes["/"] = (200, headers, '<div id="app"></div>')

    result = run_cmd(["bash", str(SCRIPT), server.url])

    assert result.returncode == 1
    fails = _fail_lines(result.stdout)
    assert len(fails) == 1
    assert "Content-Security-Policy" in fails[0]


def test_smoke_api_unreachable(server: FakeServer, run_cmd: RunCmd) -> None:
    server.routes["/api/health"] = (502, {}, "bad gateway")

    result = run_cmd(["bash", str(SCRIPT), server.url])

    assert result.returncode >= 1
    assert any("/api/health" in line for line in _fail_lines(result.stdout))


def test_smoke_spa_fallback_on_api(server: FakeServer, run_cmd: RunCmd) -> None:
    server.routes["/api/admin/auth/me"] = (200, {}, '<div id="app"></div>')

    result = run_cmd(["bash", str(SCRIPT), server.url])

    assert result.returncode >= 1
    assert any("/api/admin/auth/me" in line for line in _fail_lines(result.stdout))


def test_smoke_rejects_plain_http_remote(server: FakeServer, run_cmd: RunCmd) -> None:
    result = run_cmd(["bash", str(SCRIPT), "http://example.com"])

    assert result.returncode == 2
    assert server.requests == []
    assert "https" in result.stderr


def test_smoke_requires_argument(run_cmd: RunCmd) -> None:
    result = run_cmd(["bash", str(SCRIPT)])

    assert result.returncode == 1
    assert "用法" in result.stderr
