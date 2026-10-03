"""部署設定的結構與行為測試（INFRA-033 起：web 服務的 nginx 範本）。

結構測試直接解析 apps/web/nginx/ 底下的檔案；行為測試以 docker 執行官方 nginx 映像套用本範本，
環境沒有 Docker 時 pytest.fail（不 skip）。
"""

from __future__ import annotations

import http.server
import re
import shutil
import socket
import subprocess
import threading
import time
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from conftest import RunCmd

REPO_ROOT = Path(__file__).resolve().parents[2]
NGINX_DIR = REPO_ROOT / "apps" / "web" / "nginx"
TEMPLATE = NGINX_DIR / "default.conf.template"
SECURITY_HEADERS = NGINX_DIR / "security-headers.conf"
REAL_IP_SCRIPT = NGINX_DIR / "40-real-ip.sh"
NGINX_IMAGE = "nginx:1.27-alpine"
SECURITY_INCLUDE = "include /etc/nginx/security-headers.conf"
STATIC_LOCATIONS = ("/", "/parent/", "/assets/")


def _template() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


def location_block(conf: str, spec: str) -> str:
    """回傳 `location <spec> {` 區塊的內容（以大括號配對）；找不到時 raise KeyError。"""
    match = re.search(r"location\s+" + re.escape(spec) + r"\s*\{", conf)
    if match is None:
        raise KeyError(f"找不到 location {spec}")
    depth = 1
    start = match.end()
    for index in range(start, len(conf)):
        if conf[index] == "{":
            depth += 1
        elif conf[index] == "}":
            depth -= 1
            if depth == 0:
                return conf[start:index]
    raise KeyError(f"location {spec} 的大括號未配對")


def locations_missing_security_headers(conf: str) -> list[str]:
    """靜態內容 location 中沒有 include 安全標頭（或不存在）的清單。"""
    missing: list[str] = []
    for spec in STATIC_LOCATIONS:
        try:
            block = location_block(conf, spec)
        except KeyError:
            missing.append(spec)
            continue
        if SECURITY_INCLUDE not in block:
            missing.append(spec)
    return missing


def _csp() -> str:
    match = re.search(
        r'add_header\s+Content-Security-Policy\s+"([^"]+)"\s+always;',
        SECURITY_HEADERS.read_text(encoding="utf-8"),
    )
    assert match is not None, "security-headers.conf 沒有 Content-Security-Policy"
    return match.group(1)


def _csp_directive(csp: str, name: str) -> list[str]:
    for part in csp.split(";"):
        tokens = part.split()
        if tokens and tokens[0] == name:
            return tokens[1:]
    raise KeyError(name)


# ---------------------------------------------------------------------------
# 結構測試
# ---------------------------------------------------------------------------


def test_nginx_ws_location_upgrades() -> None:
    block = location_block(_template(), "/api/ws/")

    assert "proxy_set_header Upgrade $http_upgrade" in block
    assert 'proxy_set_header Connection "upgrade"' in block
    assert "proxy_http_version 1.1" in block
    assert "proxy_read_timeout 3600s" in block
    assert "proxy_send_timeout 3600s" in block


def test_nginx_api_location_body_limit() -> None:
    block = location_block(_template(), "/api/")

    assert "client_max_body_size 20m" in block
    assert "proxy_pass $backend_upstream" in block
    assert "proxy_read_timeout 60s" in block


def test_nginx_backend_upstream_resolved_per_request() -> None:
    conf = _template()

    assert "resolver ${NGINX_LOCAL_RESOLVERS} valid=10s ipv6=on;" in conf
    assert "set $backend_upstream ${BACKEND_URL};" in conf
    assert "proxy_pass $backend_upstream" in location_block(conf, "/api/ws/")


def test_nginx_healthz_location() -> None:
    block = location_block(_template(), "= /healthz")

    assert "return 200" in block


def test_nginx_static_locations_include_security_headers() -> None:
    assert locations_missing_security_headers(_template()) == []

    constructed = (
        "server {\n"
        f"  location / {{ {SECURITY_INCLUDE}; try_files $uri /index.html; }}\n"
        "  location /parent/ { try_files $uri /parent/index.html; }\n"
        f"  location /assets/ {{ {SECURITY_INCLUDE}; }}\n"
        "}\n"
    )
    assert locations_missing_security_headers(constructed) == ["/parent/"]


def test_nginx_csp_directives() -> None:
    csp = _csp()

    assert "frame-ancestors 'none'" in csp
    assert "https://api.line.me" in csp
    assert "object-src 'none'" in csp
    assert "https://*.r2.cloudflarestorage.com" in _csp_directive(csp, "img-src")
    assert "'unsafe-eval'" not in csp
    assert "supabase" not in csp


def test_nginx_security_headers_always() -> None:
    lines = [
        line.strip()
        for line in SECURITY_HEADERS.read_text(encoding="utf-8").splitlines()
        if line.strip().startswith("add_header")
    ]

    assert len(lines) == 6
    assert all(line.endswith("always;") for line in lines)


def _run_real_ip(run_cmd: RunCmd, tmp_path: Path, cidrs: str) -> subprocess.CompletedProcess[str]:
    return run_cmd(
        ["/bin/sh", str(REAL_IP_SCRIPT)],
        env={"TRUSTED_EDGE_CIDRS": cidrs, "REAL_IP_CONF": str(tmp_path / "real-ip.conf")},
    )


def test_nginx_real_ip_config(run_cmd: RunCmd, tmp_path: Path) -> None:
    conf = _template()
    assert "include /etc/nginx/real-ip.conf;" in conf
    assert "real_ip_header X-Forwarded-For;" in conf
    assert "real_ip_recursive on;" in conf

    ok = _run_real_ip(run_cmd, tmp_path, "10.0.0.0/8 fd00::/8")
    assert ok.returncode == 0, ok.stderr
    lines = (tmp_path / "real-ip.conf").read_text(encoding="utf-8").splitlines()
    assert lines == ["set_real_ip_from 10.0.0.0/8;", "set_real_ip_from fd00::/8;"]

    for bad in ("", "1.2.3.4/8; evil"):
        (tmp_path / "real-ip.conf").unlink(missing_ok=True)
        result = _run_real_ip(run_cmd, tmp_path, bad)
        assert result.returncode == 1, (bad, result.stdout, result.stderr)
        assert not (tmp_path / "real-ip.conf").exists()


def test_nginx_overwrites_forwarded_for() -> None:
    conf = _template()

    for spec in ("/api/", "/api/ws/"):
        assert "proxy_set_header X-Forwarded-For $remote_addr" in location_block(conf, spec)
    assert "$proxy_add_x_forwarded_for" not in conf


# ---------------------------------------------------------------------------
# docker 行為測試
# ---------------------------------------------------------------------------


def _require_docker(run_cmd: RunCmd) -> None:
    if shutil.which("docker") is None or run_cmd(["docker", "info"], timeout=30).returncode != 0:
        pytest.fail("此測試需要 Docker")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
    return port


def _nginx_mounts() -> list[str]:
    # 掛載來源不存在時 docker 會在主機上建立同名空目錄，且 nginx 會改用映像內建設定而假綠
    for path in (TEMPLATE, SECURITY_HEADERS, REAL_IP_SCRIPT):
        if not path.is_file():
            pytest.fail(f"缺少 {path.relative_to(REPO_ROOT)}")
    return [
        "-v",
        f"{TEMPLATE}:/etc/nginx/templates/default.conf.template:ro",
        "-v",
        f"{SECURITY_HEADERS}:/etc/nginx/security-headers.conf:ro",
        "-v",
        f"{REAL_IP_SCRIPT}:/docker-entrypoint.d/40-real-ip.sh:ro",
    ]


def test_nginx_config_syntax_valid(run_cmd: RunCmd) -> None:
    _require_docker(run_cmd)

    result = run_cmd(
        [
            "docker",
            "run",
            "--rm",
            "-e",
            "PORT=8080",
            "-e",
            "BACKEND_URL=http://127.0.0.1:9",
            "-e",
            "NGINX_ENTRYPOINT_LOCAL_RESOLVERS=true",
            "-e",
            "TRUSTED_EDGE_CIDRS=10.0.0.0/8",
            *_nginx_mounts(),
            NGINX_IMAGE,
            "nginx",
            "-T",
        ],
        timeout=180,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "syntax is ok" in result.stderr
    # -T 印出實際載入的設定：確認套用的是本範本與產生的 real-ip.conf，而非映像內建設定
    assert "location /api/ws/" in result.stdout
    assert "set_real_ip_from 10.0.0.0/8;" in result.stdout


class _RecordingHandler(http.server.BaseHTTPRequestHandler):
    records: list[list[str]]

    def do_GET(self) -> None:
        self.records.append(self.headers.get_all("X-Forwarded-For") or [])
        body = b"ok"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


@pytest.fixture
def recording_backend() -> Iterator[tuple[int, list[list[str]]]]:
    records: list[list[str]] = []
    handler = type("Handler", (_RecordingHandler,), {"records": records})
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1], records
    finally:
        server.shutdown()
        server.server_close()


def _forwarded_for_seen(
    run_cmd: RunCmd, backend_port: int, records: list[list[str]], trusted: str
) -> list[str]:
    web_port = _free_port()
    name = f"afterschool-nginx-probe-{web_port}"
    started = run_cmd(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            name,
            "--add-host",
            "host.docker.internal:host-gateway",
            "-p",
            f"127.0.0.1:{web_port}:8080",
            "-e",
            "PORT=8080",
            "-e",
            f"BACKEND_URL=http://host.docker.internal:{backend_port}",
            "-e",
            "NGINX_ENTRYPOINT_LOCAL_RESOLVERS=true",
            "-e",
            f"TRUSTED_EDGE_CIDRS={trusted}",
            *_nginx_mounts(),
            NGINX_IMAGE,
        ],
        timeout=180,
    )
    assert started.returncode == 0, started.stderr
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{web_port}/api/probe", headers={"X-Forwarded-For": "6.6.6.6"}
        )
        deadline = time.monotonic() + 30
        while True:
            try:
                with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310  固定本機 URL
                    assert response.status == 200
                break
            except OSError:
                if time.monotonic() > deadline:
                    logs = run_cmd(["docker", "logs", name])
                    pytest.fail(f"nginx 容器未就緒：{logs.stdout}{logs.stderr}")
                time.sleep(0.5)
    finally:
        run_cmd(["docker", "rm", "-f", name], timeout=60)
    assert len(records) == 1, records
    return records.pop()


def test_nginx_spoofed_forwarded_for_not_passed(
    run_cmd: RunCmd, recording_backend: tuple[int, list[list[str]]]
) -> None:
    _require_docker(run_cmd)
    backend_port, records = recording_backend

    untrusted = _forwarded_for_seen(run_cmd, backend_port, records, "203.0.113.0/24")
    assert len(untrusted) == 1, untrusted
    assert untrusted[0] != "6.6.6.6"
    assert "6.6.6.6" not in untrusted[0]

    # 對照組：信任所有來源時，nginx 會採信用戶端自填值（證明測試能偵測信任設定）
    trusted = _forwarded_for_seen(run_cmd, backend_port, records, "0.0.0.0/0")
    assert trusted == ["6.6.6.6"]
